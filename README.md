# xtool-on-linux

Build, sign and ship an iPhone app from Linux, with no Mac on the desk.

These are the scripts and notes behind my reel about building my assistant app on Linux with
[xtool](https://github.com/xtool-org/xtool). xtool does the heavy lifting. This repo is everything
I had to add around it to get widgets, Live Activities, an Apple Watch app, Siri and TestFlight.

| Clean build | Peak memory | App size |
| :---------: | :---------: | :------: |
| **3 min 37 s** | 4.5 GB | 29 MB |

Measured on a clean build of my own app (217.36 s).

## What works

| Feature | Status | Where it is built |
| --- | :---: | --- |
| iOS app | ✅ | Linux, plain xtool |
| Widgets | ✅ | Linux, xtool's app-extension support |
| Live Activities (Lock Screen and Dynamic Island) | ✅ | Linux, same extension |
| Siri, Shortcuts and Spotlight (App Intents) | ✅ | Linux, with [the workaround](#the-siri-catch) |
| Apple Watch app | ✅ | compiled on a GitHub macOS runner, assembled and signed on Linux |
| Watch-face complication | ⚠️ | builds and signs, not yet confirmed on a real watch face |
| Install over the air or over USB | ✅ | Linux |
| TestFlight | ✅ | signed on Linux, uploaded by a GitHub macOS runner |

"No Mac" means no Mac of your own. Three steps have no Linux equivalent and borrow a GitHub-hosted
macOS runner for a few seconds each: compiling the watch app, compiling the app icon (`actool`),
and the App Store upload (`altool`). Everything else runs on your Linux box.

## The Siri catch

App Intents compile fine on Linux, and then Siri and Shortcuts act as if the app has none. No
error anywhere.

The reason: on a Mac, Xcode runs a closed-source tool (`appintentsmetadataprocessor`) after the
compile. It writes an index of your intents into the app at `Metadata.appintents/`. iOS only reads
that index. No index, no Siri.

The input to that tool is not secret, though. The Swift compiler already emits it, on Linux too
(`-emit-const-values`). So [`appintents/appintents-gen.py`](appintents/appintents-gen.py) writes the
index itself, and `ios-ota` puts it into the app before signing. It is checked against a bundle
that Apple's real tool generated:

```console
$ python3 appintents/test_golden.py
ok — matches Apple's golden bundle (7 known differences)
```

How it works, what the known differences are, and what is not covered:
[docs/SIRI.md](docs/SIRI.md).

## Quick start

```bash
git clone https://github.com/cyberneel/xtool-on-linux
export PATH="$PWD/xtool-on-linux/bin:$PATH"

cd MyApp          # any xtool project
ios-ota .         # build, add Siri metadata, sign, publish an install page, USB-install if plugged in
```

That is the short version. The one-time setup (Swift, xtool, the SDK, signing identity) is in
[docs/SETUP.md](docs/SETUP.md), along with the watch app and TestFlight steps.

## What is in here

```
bin/
  ios-ota               the pipeline: build, Siri metadata, watch embed, sign, publish
  ios-testflight        same pipeline, distribution-signed, then uploaded
  xtool-gen-appintents  runs the Siri generator against a built .app
  xtool-embed-watch     builds the watch app on CI and embeds it
  provision.py          signing certificates and profiles via the App Store Connect API
  ios-upload            couriers a signed .ipa to a macOS runner for altool
  ios-appicon           compiles the app icon on a macOS runner
  ios-host              finds which machine the iPhone is plugged into
  gh-retry              gh, retried on GitHub 5xx errors
appintents/             the Siri workaround: generator, protocol list, golden test
ci/                     the GitHub workflows, to copy into repos of your own
systemd/                share a plugged-in iPhone with another machine
example/                config files for an app with a widget and a watch app
docs/                   SETUP.md and SIRI.md
```

## What you need

- A Linux machine. I use Arch. xtool targets Ubuntu, so Arch needs a few extra packages
  (listed in the setup doc).
- A paid Apple Developer account. The scripts sign through an App Store Connect API key.
- Xcode's `.xip` from Apple, for the SDK. It is not included here and you should not get it
  anywhere else.
- A GitHub account, only for the watch app and TestFlight.

Versions I run: xtool 1.17.0, Swift 6.3.3, the Xcode 26.6 SDK, rcodesign 0.29.0.

## Not included

No Apple binaries, no SDK, no signing keys. The scripts create your own certificates and keep them
in `~/.local/share/ios-ota/`, which never belongs in a repo.

## Credit

[xtool](https://github.com/xtool-org/xtool) by Kabir Oberai and contributors is what makes any of
this possible. The Siri generator's output format started from
[xtool PR #217](https://github.com/xtool-org/xtool/pull/217).

MIT licensed. Not affiliated with Apple.
