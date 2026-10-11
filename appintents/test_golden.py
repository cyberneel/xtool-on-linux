#!/usr/bin/env python3
"""Diff this generator's output against a real Apple-generated bundle.

reference/golden/ holds Apple's own extract.actionsdata for reference/golden/GoldenIntents.swift,
produced by the closed appintentsmetadataprocessor on an Xcode 26.6 macOS runner (the
`appintents-golden` workflow in ci/watch-link-ci/). Running our generator over the same
.swiftconstvalues must reproduce it, apart from the deliberate differences listed below.

    python3 test_golden.py
"""
import json, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
GOLD = HERE / "reference/golden"

# Differences we intend to have. Anything else is a regression.
EXPECTED = {
    # Xcode fills these per-bundle at link time. We populate them so an intent that lives in a
    # shared module still resolves when a widget / Live Activity button invokes it from an appex.
    ".actions.AskGoldenIntent.mangledTypeNameByBundleIdentifier.com.example.golden: EXTRA",
    ".actions.AskGoldenIntent.mangledTypeNameByBundleIdentifierV2.com.example.golden: EXTRA",
    ".actions.OpenGoldenIntent.mangledTypeNameByBundleIdentifier.com.example.golden: EXTRA",
    ".actions.OpenGoldenIntent.mangledTypeNameByBundleIdentifierV2.com.example.golden: EXTRA",
    # We are honest about who generated the file.
    ".generator.name: apple",
    ".generator.version: apple",
    # EntityQuery metadata is not implemented; it only matters for entity-typed parameters.
    ".queries.GoldenNoteQuery: MISSING",
}


def diff(a, b, path=""):
    out = []
    if type(a) is not type(b):
        return [f"{path}: type {type(a).__name__} vs {type(b).__name__}"]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in b:
                out.append(f"{path}.{k}: MISSING")
            elif k not in a:
                out.append(f"{path}.{k}: EXTRA")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: len {len(a)} vs {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: apple={json.dumps(a)[:40]} ours={json.dumps(b)[:40]}")
    return out


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(HERE / "appintents-gen.py"),
                        "--module", "GoldenIntents", "--bundle-id", "com.example.golden",
                        "--out", tmp, str(GOLD / "Golden.swiftconstvalues")],
                       check=True, capture_output=True)
        ours = json.load(open(pathlib.Path(tmp) / "extract.actionsdata"))
    gold = json.load(open(GOLD / "extract.actionsdata"))

    found = diff(gold, ours)
    unexpected = [d for d in found if not any(d.startswith(e) for e in EXPECTED)]

    # The regression that hid every xtool app's actions from the Shortcuts action library.
    for action in ours["actions"].values():
        assert action["effectiveBundleIdentifiers"] == [], \
            "effectiveBundleIdentifiers must stay empty or the Shortcuts app hides the action"

    if unexpected:
        print(f"FAIL: {len(unexpected)} unexpected difference(s) from Apple's output:")
        for d in unexpected:
            print("  ", d)
        return 1
    print(f"ok — matches Apple's golden bundle ({len(found)} known differences)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
