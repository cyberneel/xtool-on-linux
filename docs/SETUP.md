# Setup

From a bare Linux box to an app on your iPhone. Steps 1 to 4 are one-time.

- [1. System packages](#1-system-packages)
- [2. Swift](#2-swift)
- [3. xtool and the SDK](#3-xtool-and-the-sdk)
- [4. This repo's tools](#4-this-repos-tools)
- [5. Build and install](#5-build-and-install)
- [6. Widgets and Live Activities](#6-widgets-and-live-activities)
- [7. Siri and Shortcuts](#7-siri-and-shortcuts)
- [8. Apple Watch app](#8-apple-watch-app)
- [9. TestFlight](#9-testflight)
- [10. iPhone plugged into a different machine](#10-iphone-plugged-into-a-different-machine)

## 1. System packages

Arch:

```bash
sudo pacman -S usbmuxd libimobiledevice fuse2 libxml2-legacy socat zip openssl github-cli \
  tailscale python-pipx python-pillow python-pyjwt python-requests python-cryptography

# Swift's Linux toolchain is built for Ubuntu and looks for this name
sudo ln -s libncursesw.so.6 /usr/lib/libncurses.so.6
```

`fuse2` is for the xtool AppImage. `libxml2-legacy` and the ncurses link are for the Swift
toolchain. On Ubuntu those three are not needed; install the rest under their Ubuntu names.

Then two tools that are not in the Arch repos:

```bash
pipx install pymobiledevice3     # installs apps over USB

# rcodesign: code signing without a Mac
curl -sfL https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign/0.29.0/apple-codesign-0.29.0-x86_64-unknown-linux-musl.tar.gz \
  | tar xz --strip-components=1 -C ~/.local/bin --wildcards '*/rcodesign'
```

## 2. Swift

Install [swiftly](https://www.swift.org/install/linux/). On Arch it does not recognise the
distro, so tell it to use the Ubuntu toolchain:

```bash
./swiftly init -y --platform ubuntu24.04
. ~/.local/share/swiftly/env.sh     # add to your shell profile
swift --version
```

## 3. xtool and the SDK

1. Put the xtool AppImage from the [releases page](https://github.com/xtool-org/xtool/releases)
   at `~/.local/bin/xtool` and `chmod +x` it.
2. Download Xcode's `.xip` from [Apple](https://developer.apple.com/download/all/). I used
   `Xcode_26.6_Universal.xip`.
3. Create an App Store Connect API key (Users and Access → Integrations → App Store Connect API)
   with the Developer role or higher.

```bash
xtool auth login                                  # choose the API key option
xtool sdk install ~/Downloads/Xcode_26.6_Universal.xip
```

Check it with a fresh project and the phone on USB:

```bash
xtool new Hello && cd Hello && xtool dev
```

If that puts an app on your phone, xtool is working and the rest of this repo applies.

## 4. This repo's tools

```bash
git clone https://github.com/cyberneel/xtool-on-linux
export PATH="$PWD/xtool-on-linux/bin:$PATH"       # add to your shell profile

provision.py identity      # creates your development certificate; the key stays on this machine
```

Everything the scripts create lives in `~/.local/share/ios-ota/`: keys, certificates, profiles and
the install pages. Back it up, and never commit it.

The scripts read the API key that `xtool auth login` stored, so they need API-key sign-in.

For over-the-air installs the phone has to reach your Linux box over HTTPS with a real
certificate. The scripts use [Tailscale](https://tailscale.com) for that:

```bash
sudo tailscale set --operator=$USER   # so `tailscale serve` works without sudo
```

Turn on MagicDNS and HTTPS certificates in the Tailscale admin console, and put the iPhone on the
same tailnet.

## 5. Build and install

```bash
cd MyApp
ios-ota .
```

What it does:

1. `xtool dev build --ipa`
2. Rewrites the bundle ID to `XTL-XXXXXXXX.<your id>` and bumps the build number. That is the ID
   xtool itself installs under, so USB and over-the-air installs update the same app and keep its
   data.
3. Adds the toolchain keys App Store validation expects (`DTSDKName` and friends).
4. Generates the Siri index ([SIRI.md](SIRI.md)).
5. Embeds the watch app, if the project has one.
6. Gives each extension and the watch app its own bundle ID, profile and entitlements.
7. Signs with `rcodesign`.
8. Publishes an install page at `https://<your-machine>.<tailnet>.ts.net/<App>/`. Open it on the
   phone and tap Install.
9. If an iPhone is on USB, installs there too.

Optional files next to `xtool.yml` (samples in [`example/`](../example)):

| File | What it does |
| --- | --- |
| `Icon.png` | 1024 px icon, used for the install page, the watch app and TestFlight |
| `ios-ota.infoplist.json` | Info.plist keys merged in before signing. xtool's own merge drops arrays of dictionaries, such as URL schemes and Home Screen quick actions |
| `ios-ota.entitlements.json` | entitlements merged onto the profile's. `null` removes a key |
| `ios-ota.watch` | one line naming the watch app. Turns on step 5 |
| `ios-ota.watch.infoplist.json` | extra Info.plist keys for the watch app |
| `ios-ota.watch.activities` | `ActivityAttributes` type names whose Live Activity should open the watch app |
| `AppIntentVocabulary.plist` | SiriKit example phrases |

Good to know:

- Profiles are cached in `~/.local/share/ios-ota/profiles/` and last a year. Delete one to have it
  recreated. Do the same after registering a new device.
- Do not `xtool install` an `.ipa` that `ios-ota` made. xtool adds its prefix again and you get a
  second copy of the app.
- The `DT_*` values at the top of `ios-ota` describe the Xcode 26.6 SDK. Change them if you
  install a different SDK. The `appintents-golden` workflow prints the right values for the
  runner's Xcode.

## 6. Widgets and Live Activities

Nothing from this repo is needed to build them. xtool supports app extensions. A widget and a Live
Activity both ship in one WidgetKit extension:

- `Package.swift`: three targets. The app, the widget (both library products) and a shared target
  holding the `ActivityAttributes`. See [`example/Package.swift`](../example/Package.swift).
- `xtool.yml`: an `extensions:` entry with `product`, `bundleID` and `infoPath`.
  See [`example/xtool.yml`](../example/xtool.yml).
- App Info.plist: `NSSupportsLiveActivities` = true.
- Widget Info.plist: `NSExtension.NSExtensionPointIdentifier` = `com.apple.widgetkit-extension`.
- Widget code: an `@main WidgetBundle`. The app starts activities with `Activity.request`.

What `ios-ota` adds: the extension gets a child bundle ID, its own profile, and the same version
number as the app. iOS refuses to load an extension whose version differs, and it does so silently.

## 7. Siri and Shortcuts

Automatic. `ios-ota` runs `xtool-gen-appintents` on every build. Background and limits are in
[SIRI.md](SIRI.md).

## 8. Apple Watch app

The watch app is the one part that cannot be compiled on Linux today. Code for current watches
has to come from Apple's compiler: a Linux-compiled build crashes at launch with a pointer
authentication failure. The App Store also wants two specific slices (`arm64` and `arm64_32`).

So the watch source goes to a GitHub macOS runner, which compiles it and sends the binary back.
A run takes about 25 seconds. Assembly and signing still happen on Linux.

One-time:

```bash
# 1. Make a repo from ci/watch-link-ci/ (public repos get free macOS minutes)
gh repo create <you>/watch-link-ci --public
#    ...copy the contents of ci/watch-link-ci/ into it, commit, push

# 2. A key shared between your machine and the runner
openssl rand -base64 32 > ~/.local/share/ios-ota/watch-src.key
chmod 600 ~/.local/share/ios-ota/watch-src.key
gh secret set SRC_KEY --repo <you>/watch-link-ci --body "$(cat ~/.local/share/ios-ota/watch-src.key)"

export WATCH_CI_REPO=<you>/watch-link-ci        # add to your shell profile
```

Your source is AES-256 encrypted with that key before it leaves your machine and travels as a
workflow input. On a public repo the ciphertext is visible to anyone, and the compiled binary is
a one-day build artifact. If that bothers you, make the repo private and pay for the minutes.

Per project:

- Put the watch app's Swift files in `Sources/<WatchName>/`, flat, with an `@main` SwiftUI `App`.
  Do not add it to `Package.swift`. Only the `.swift` files in that folder are sent, so it cannot
  import your shared module.
- `echo <WatchName> > ios-ota.watch`
- Run `ios-ota .` as usual.

Things that will otherwise waste an afternoon:

- **Register the watch as a device.** It is separate from the phone. Without it the app shows up
  under Available Apps and Install does nothing. `pymobiledevice3 companion list` prints the
  watch's UDID. Add it in the developer portal, then delete the cached profiles.
- **The watch app needs an icon.** Without one it never installs. `Icon.png` is used if present,
  otherwise a placeholder.
- **Complications:** put a WidgetKit extension in `Sources/<WatchName>Complication/` and it is
  built and embedded too. This builds and signs. I have not confirmed it on a real watch face.

## 9. TestFlight

Apple has no Linux uploader, so the signed `.ipa` is handed to a macOS runner that runs `altool`.

One-time:

```bash
# 1. A PRIVATE repo from ci/ios-upload/. Your signed build passes through it.
gh repo create <you>/ios-upload --private
#    ...copy the contents of ci/ios-upload/ into it, commit, push
gh secret set ASC_KEY_ID    --repo <you>/ios-upload
gh secret set ASC_ISSUER_ID --repo <you>/ios-upload
gh secret set ASC_KEY_P8    --repo <you>/ios-upload < AuthKey_XXXXXXXXXX.p8

export IOS_UPLOAD_REPO=<you>/ios-upload          # add to your shell profile

# 2. A distribution certificate
provision.py dist-identity
```

3. Create the app record on the App Store Connect website, for bundle ID
   `XTL-XXXXXXXX.<your id>`. Run `ios-ota .` once first so that ID is registered. The API cannot
   create app records.

Per release:

```bash
ios-appicon .        # once, and again whenever Icon.png changes
ios-testflight .     # or: ios-ota --both .   (install page and TestFlight from one compile)

# optional: set What to Test, add the public groups, submit for beta review
TF_NOTE="What changed" provision.py release ~/.local/share/ios-ota/testflight/MyApp.ipa
```

The `.ipa` sits in the private repo as a release asset only while the upload runs, then it is
deleted. macOS minutes on a private repo are billed at 10x. A self-hosted Mac runner labelled
`xtool-mac` is used instead when one is online.

`ios-ota` already handles the validation failures I ran into: missing toolchain keys, watch
bundles labelled as iOS, extensions without the `arm64` capability, icons that are not a compiled
asset catalog, icons with transparency, and SwiftPM resource bundles without an Info.plist.

## 10. iPhone plugged into a different machine

If the phone is on USB somewhere else, share that machine's `usbmuxd` over the tailnet.

On the machine with the phone: install [`systemd/usbmux-share.service`](../systemd/usbmux-share.service)
with its own tailnet IP filled in. It listens on that IP only.

On the build machine, either:

- list the sharing machines in `~/.config/ios-drive.conf`
  (see [`example/ios-drive.conf`](../example/ios-drive.conf)). `ios-host` then finds whichever one
  has the phone, and `ios-ota` uses it. `ios-host --all` shows what each one sees.
- or install [`systemd/usbmux-bridge.service`](../systemd/usbmux-bridge.service), which makes the
  remote phone look local to every tool. Do not run a local `usbmuxd` alongside it.
