# The Siri workaround

## The problem

You write an `AppIntent`. It compiles on Linux. You install the app. Siri, Shortcuts and Spotlight
show nothing, and nothing logs an error.

iOS does not find intents by looking at your binary. It reads an index inside the app:

```
MyApp.app/Metadata.appintents/
  extract.actionsdata    JSON: every intent, parameter, entity, enum and shortcut phrase
  version.json
```

On a Mac, Xcode writes that index in two stages:

1. `swiftc` records the constant values of every type that conforms to an App Intents protocol
   into `.swiftconstvalues` files.
2. `appintentsmetadataprocessor` turns those files into `extract.actionsdata`.

Stage 2 is closed source and macOS only. Stage 1 is the ordinary open-source compiler, and it
works on Linux. So only stage 2 needs replacing.

## The fix

[`appintents-gen.py`](../appintents/appintents-gen.py) is stage 2, in about 400 lines of Python.

`xtool-gen-appintents` wires it up. `ios-ota` calls it for you before signing, so the index is
covered by the signature:

```bash
# stage 1: a side build that only exists to emit the const values
swift build --swift-sdk arm64-apple-ios -c debug \
  -Xswiftc -Xfrontend -Xswiftc -const-gather-protocols-file \
  -Xswiftc -Xfrontend -Xswiftc appintents/appintents-protocols.json \
  -Xswiftc -emit-const-values

# stage 2: write the index into the built app
appintents/appintents-gen.py --module MyApp --bundle-id com.example.myapp \
  --out MyApp.app/Metadata.appintents $(find .build -name '*.swiftconstvalues')
```

To run it by hand against a built app:

```bash
xtool-gen-appintents <project-dir> <path/to/MyApp.app> <module> <bundle-id>
```

It is a no-op for a project with no intents. Set `XTOOL_SKIP_APPINTENTS=1` to skip it.

## What turned out to matter

Each of these cost a round of build, sign, install and stare at the phone.

- **The file is JSON.** The `.actionsdata` extension suggests a binary plist. iOS silently skips a
  binary plist.
- **`mangledTypeName` must be exact.** It is how iOS finds the Swift type to run. Wrong, and the
  action shows up but does nothing. The generator takes it straight from the compiler output
  instead of rebuilding it from source text, so generics and cross-module types come out right.
- **`effectiveBundleIdentifiers` must be empty.** Putting the app's own bundle ID there hides every
  action from the Shortcuts action library, while Siri and Spotlight keep working. That made it
  look like a display bug for a long time.
- **`mangledTypeNameByBundleIdentifier` must be filled in**, for the app and for every extension.
  Otherwise an intent that lives in a shared module and is triggered by a widget or Live Activity
  button fails with "There is no metadata for … in …", and the button does nothing.
- **Every extension gets its own index**, keyed to its own bundle ID.
- **SiriKit extensions are skipped.** An `com.apple.intents-service` extension is not App Intents,
  and App Store processing rejects the build if it carries this index.

## How it is checked

`appintents/reference/golden/` holds a real index that Apple's tool produced for
`GoldenIntents.swift`, plus the `.swiftconstvalues` it was produced from. `test_golden.py` runs the
generator over the same input and compares:

```console
$ python3 appintents/test_golden.py
ok — matches Apple's golden bundle (7 known differences)
```

The 7 known differences:

| Count | Difference | Why |
| :---: | --- | --- |
| 4 | extra `mangledTypeNameByBundleIdentifier` entries | Xcode fills these at link time; the generator fills them up front |
| 2 | generator name and version | it says who wrote the file |
| 1 | no `EntityQuery` metadata | not implemented, see below |

The golden bundle comes from [`ci/watch-link-ci/.github/workflows/appintents-golden.yml`](../ci/watch-link-ci/.github/workflows/appintents-golden.yml),
which runs Apple's tool on a GitHub macOS runner. Only the sample file is compiled there.

## What is not covered

- **`EntityQuery` metadata is not generated.** Plain intents, parameters, enums, entities and
  App Shortcuts phrases are. An intent with an entity-typed parameter that needs its query in the
  index may not behave like an Xcode build.
- **The format is undocumented.** Apple can change it in any iOS release. When Xcode updates,
  rerun the golden workflow, replace `reference/golden/` and see what the test reports.
- Tested on-device with the Xcode 26.6 SDK on an iPhone running the iOS 27 beta: actions appear in
  the Shortcuts library, run, and respond through Siri. Other versions are untested.

## Background reading

- [xtool PR #217](https://github.com/xtool-org/xtool/pull/217): an earlier attempt that parses
  Swift source with SwiftSyntax. The output schema here started from it.
- [swift-build](https://github.com/swiftlang/swift-build): Apple's open-source build system. It
  shows how Xcode invokes the closed tool and which protocols it gathers.
