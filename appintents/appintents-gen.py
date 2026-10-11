#!/usr/bin/env python3
"""Generate a Metadata.appintents bundle from swiftc's .swiftconstvalues files.

This is the Linux-native replacement for Apple's closed appintentsmetadataprocessor.
It reads the JSON that `swiftc -emit-const-values` produces (which works on Linux) and
emits `extract.actionsdata` + `version.json` in the format iOS's Shortcuts/Siri indexer reads.

Output schema is from xtool PR #217 (reverse-engineered from a shipping IPA and validated
on-device). Unlike PR #217's SwiftSyntax scanner, we take mangledTypeName straight from the
compiler, so cross-module names and generics are correct for free.

Usage:
  appintents-gen.py --module <ModuleName> --bundle-id <id> --out <App.app/Metadata.appintents> \
      <file1.swiftconstvalues> [<file2.swiftconstvalues> ...]
"""
import argparse, json, os, sys

# The file is JSON despite the .actionsdata extension. iOS SILENTLY skips a binary-plist
# bundle (PR #217's root-cause bug), so JSON is mandatory.
ACTIONSDATA_VERSION = 1
VERSION_JSON = "3.0"
GENERATOR = "xtool-appintents-gen"

def conforms(entry, suffix):
    return any(c == f"AppIntents.{suffix}" or c.endswith(f".{suffix}")
               for c in entry.get("conformances", []))

def localizable(s):
    return {"key": s, "alternatives": []}

def availability():
    return {"LNPlatformNameWildcard": {"introducedVersion": "*"}}

def prop(entry, label):
    for p in entry.get("properties", []):
        if p.get("label") == label:
            return p
    return None

def raw_value(p):
    """Extract a literal string from a property/argument whose valueKind is RawLiteral."""
    if p and p.get("valueKind") == "RawLiteral":
        return p.get("value")
    return None

def init_args(p):
    """Return {label: argument-dict} for a property whose value is an InitCall."""
    if p and isinstance(p.get("value"), dict):
        return {a.get("label"): a for a in p["value"].get("arguments", [])}
    return {}

def generic_inner(type_str, wrapper):
    """`AppIntents.IntentParameter<Swift.String>` + wrapper=IntentParameter -> `Swift.String`."""
    pfx = f"{wrapper}<"
    i = type_str.find(pfx)
    if i < 0:
        return None
    return type_str[i + len(pfx):type_str.rfind(">")]

# ---- parameters ----------------------------------------------------------------

# Primitive type codes, read off a golden Apple-generated bundle (see GOLDEN.md). Anything not
# listed falls back to 0, which is what String uses and what iOS tolerated before we had codes.
TYPE_IDENTIFIERS = {"Swift.String": 0, "Swift.Int": 2}

# What a parameter can be fed from in the Shortcuts editor. Copied verbatim per declared type from
# a golden bundle rather than derived — the identifiers don't map cleanly onto Swift types (a
# String parameter lists 0, [2] and 2), so guessing them would be inventing a rule Apple doesn't use.
_PRIM = lambda n: {"kindValue": 0, "valueType": {"primitive": {"wrapper": {"typeIdentifier": n}}}}
_ARRAY_OF = lambda n: {"kindValue": 0, "valueType": {"array": {"wrapper": {
    "capabilities": 3, "memberValueType": {"primitive": {"wrapper": {"typeIdentifier": n}}}}}}}
RESOLVABLE_INPUT_TYPES = {
    "Swift.String": [_PRIM(0), _ARRAY_OF(2), _PRIM(2)],
    "Swift.Int": [_PRIM(7), _PRIM(0), _PRIM(2)],
}


def value_type(inner, enum_names):
    """The `valueType` wrapper for a parameter of Swift type `inner`."""
    bare = inner.removeprefix("Swift.Optional<").removesuffix(">")
    if bare.split(".")[-1] in enum_names:
        return {"linkEnumeration": {"wrapper": {"identifier": bare.split(".")[-1]}}}
    return {"primitive": {"wrapper": {"typeIdentifier": TYPE_IDENTIFIERS.get(bare, 0)}}}


def has_default(arg):
    """The `default:` argument is always present in the init call; NilLiteral means there is none."""
    return bool(arg) and arg.get("valueKind") != "NilLiteral"


def default_metadata(arg, inner):
    """Apple encodes @Parameter defaults as a 2-element typeSpecificMetadata pair, NOT as the
    `defaultValueExpression` string this generator used to emit (that key is absent from every
    golden bundle)."""
    if not has_default(arg):
        return []
    if arg.get("valueKind") == "Enum":
        name = (arg.get("value") or {}).get("name")
        wrapped = {"string": {"wrapper": name}} if name else None
    else:
        raw = raw_value(arg)
        if raw is None:
            return []
        bare = inner.removeprefix("Swift.Optional<").removesuffix(">")
        if bare == "Swift.Int":
            try:
                wrapped = {"int": {"wrapper": int(raw)}}
            except ValueError:
                return []
        elif bare == "Swift.Bool":
            wrapped = {"bool": {"wrapper": raw == "true"}}
        else:
            wrapped = {"string": {"wrapper": raw}}
    if wrapped is None:
        return []
    return ["LNValueTypeSpecificMetadataKeyDefaultValue", wrapped]


def scan_parameters(entry, enum_names=()):
    """@Parameter properties surface as stored `_name` props of type IntentParameter<T>."""
    params = []
    for p in entry.get("properties", []):
        label = p.get("label", "")
        t = p.get("type", "")
        if not (label.startswith("_") and t.startswith("AppIntents.IntentParameter<")):
            continue
        name = label[1:]
        inner = generic_inner(t, "AppIntents.IntentParameter") or "Swift.String"
        is_optional = inner.startswith("Swift.Optional<") or inner.endswith("?")
        args = init_args(p)
        title = raw_value(args.get("title")) or name
        param = {
            "name": name,
            "isOptional": is_optional,
            "isInput": False,
            "capabilities": 1 if has_default(args.get("default")) else 0,
            "dynamicOptionsSupport": 0,
            "inputConnectionBehavior": 0,
            "title": localizable(title),
            "valueType": value_type(inner, enum_names),
            "resolvableInputTypes": RESOLVABLE_INPUT_TYPES.get(
                inner.removeprefix("Swift.Optional<").removesuffix(">"), []),
            "typeSpecificMetadata": default_metadata(args.get("default"), inner),
        }
        desc = raw_value(args.get("description"))
        if desc:
            param["parameterDescription"] = localizable(desc)
        params.append(param)
    return params

# ---- intents / entities / enums / shortcuts ------------------------------------

def returns_value(entry):
    for a in entry.get("associatedTypeAliases", []):
        if a.get("typeAliasName") == "PerformResult" and "ReturnsValue" in a.get("substitutedTypeName", ""):
            return True
    return False

# IntentModes bits, read out of Apple's own extractor (Xcode 17F113, extractSupportedModes):
# .background 1, .foreground / .foreground(.immediate) 2, .foreground(.deferred) 4, .foreground(.dynamic) 8.
MODE_BITS = {"background": 1, "foreground": 2, "immediate": 2, "deferred": 4, "dynamic": 8}

def supported_modes(entry, opens_app):
    """An explicit `static let supportedModes` (iOS 26) wins; else the openAppWhenRun mapping."""
    p = prop(entry, "supportedModes")
    if not p:
        # 2 == runs by opening the app, 1 == runs in the background (from a golden bundle).
        return 2 if opens_app else 1
    bits = 0
    for item in (p["value"] if p.get("valueKind") == "Array" else [p]):
        v = item["value"]
        args = v.get("arguments") or []   # .foreground(.dynamic) is a call; .background a plain member
        bits |= MODE_BITS[args[0]["value"]["memberLabel"] if args else v["memberLabel"]]
    return bits

def action_dict(entry, module, bundle_ids=(), enum_names=()):
    type_name = entry["typeName"].split(".")[-1]
    # Qualify with the type's OWN module, not the app module: an intent defined in a shared
    # module (and invoked from a widget / Live Activity button) is unresolvable when the metadata
    # claims it lives in the app — the system reports "There is no metadata for <Intent>".
    qualified = entry["typeName"] if "." in entry["typeName"] else f"{module}.{type_name}"
    mangled = entry["mangledTypeName"]
    title = raw_value(prop(entry, "title")) or type_name
    desc = raw_value(prop(entry, "description")) or ""
    rv = returns_value(entry)
    opens_app = raw_value(prop(entry, "openAppWhenRun")) == "true"
    d = {
        "identifier": type_name,
        "fullyQualifiedTypeName": qualified,
        "mangledTypeName": mangled,
        "mangledTypeNameV2": mangled,
        # Which binary holds the type, per bundle. Xcode fills this in; leaving it empty is fine
        # for intents defined in the app's own module, but an intent that lives in a SHARED module
        # and is invoked from an appex (a widget / Live Activity button) fails the lookup with
        # "There is no metadata for <Intent> in <bundle>" and the button silently does nothing.
        "mangledTypeNameByBundleIdentifier": {b: mangled for b in bundle_ids},
        "mangledTypeNameByBundleIdentifierV2": {b: mangled for b in bundle_ids},
        "title": localizable(title),
        "visibilityMetadata": {"isDiscoverable": True, "assistantOnly": False},
        "availabilityAnnotations": availability(),
        "isDiscoverable": True,
        "isAuthPolExplicit": False,
        "authenticationPolicy": 0,
        "openAppWhenRun": opens_app,
        # Golden bundles carry 0 here even for an intent returning a value; the old `4` was a guess.
        "outputFlags": 0,
        "presentationStyle": 0,
        "supportedModes": supported_modes(entry, opens_app),
        "requiredCapabilities": [],
        # MUST stay empty. Listing the app's own bundle id here is what kept every xtool app's
        # actions out of the Shortcuts action library — Siri, Spotlight and execution all still
        # worked, so it looked like a rendering problem. Apple's golden bundles leave this empty;
        # bisected on-device 2026-08-02, and emptying it alone brought all 11 of the app's actions back.
        # (mangledTypeNameByBundleIdentifier above is a different key and DOES need populating.)
        "effectiveBundleIdentifiers": [],
        "systemProtocols": [],
        "systemProtocolMetadata": [],
        "systemProtocolMetadataV2": [],
        "typeSpecificMetadata": [],
        "assistantDefinedSchemas": [],
        "assistantDefinedSchemaTraits": [],
        "parameters": scan_parameters(entry, enum_names),
    }
    if desc:
        d["descriptionMetadata"] = {"descriptionText": localizable(desc), "searchKeywords": []}
    if rv:
        d["outputType"] = {"primitive": {"wrapper": {"typeIdentifier": 0}}}
    return d

def display_type_name(entry, fallback):
    """AppEntity/AppEnum both name themselves via `typeDisplayRepresentation`, and both encode it
    as `displayTypeName` — NOT the `title` this generator used to emit."""
    args = init_args(prop(entry, "typeDisplayRepresentation"))
    return localizable(raw_value(args.get("name")) or fallback)


def entity_dict(entry, module):
    type_name = entry["typeName"].split(".")[-1]
    mangled = entry["mangledTypeName"]
    qualified = entry["typeName"] if "." in entry["typeName"] else f"{module}.{type_name}"
    d = {
        "typeName": type_name,
        "fullyQualifiedTypeName": qualified,
        "mangledTypeName": mangled,
        "mangledTypeNameByBundleIdentifier": {},
        "displayTypeName": display_type_name(entry, type_name),
        "availabilityAnnotations": availability(),
        "assistantDefinedSchemas": [],
        "effectiveBundleIdentifiers": [],
        "properties": [],
        "requiredCapabilities": [],
        "systemProtocolMetadata": [],
        "systemProtocolMetadataV2": [],
        "transient": False,
        "visibilityMetadata": {"isDiscoverable": True, "assistantOnly": False},
    }
    query = prop(entry, "defaultQuery")
    if query and query.get("type"):
        d["defaultQueryIdentifier"] = query["type"]
    return d


def enum_dict(entry, module):
    type_name = entry["typeName"].split(".")[-1]
    qualified = entry["typeName"] if "." in entry["typeName"] else f"{module}.{type_name}"
    # `caseDisplayRepresentations` is emitted as a list of {key,value} pairs (the key being the
    # enum case, the value its title) — not as an init call with labelled arguments.
    cases = []
    cdr = prop(entry, "caseDisplayRepresentations")
    for pair in (cdr or {}).get("value") or []:
        if not isinstance(pair, dict):
            continue
        name = ((pair.get("key") or {}).get("value") or {}).get("name")
        if not name:
            continue
        title = raw_value(pair.get("value")) or name
        cases.append({"identifier": name,
                      "displayRepresentation": {"title": localizable(title)}})
    return {
        "identifier": type_name,
        "fullyQualifiedTypeName": qualified,
        "mangledTypeName": entry["mangledTypeName"],
        "mangledTypeNameByBundleIdentifier": {},
        "displayTypeName": display_type_name(entry, type_name),
        "cases": cases,
        "isSystem": False,
        "availabilityAnnotations": availability(),
        "assistantDefinedSchemas": [],
        "effectiveBundleIdentifiers": [],
        "visibilityMetadata": {"isDiscoverable": True, "assistantOnly": False},
    }

def normalise_phrase(segments):
    """InterpolatedStringLiteral segments -> `Ping ${applicationName}` template."""
    out = ""
    for seg in segments:
        if seg.get("valueKind") == "RawLiteral":
            out += seg.get("value", "")
        elif seg.get("valueKind") == "Enum":
            out += "${" + seg.get("value", {}).get("name", "") + "}"
    return out

def scan_shortcuts(entry):
    """AppShortcutsProvider.appShortcuts (a Builder) -> list of autoShortcut dicts."""
    shortcuts = []
    p = prop(entry, "appShortcuts")
    if not p or not isinstance(p.get("value"), dict):
        return shortcuts
    for member in p["value"].get("members", []):
        el = member.get("element", {})
        args = {a.get("label"): a for a in el.get("value", {}).get("arguments", [])}
        intent_arg = args.get("intent", {})
        intent_type = intent_arg.get("type", "").split(".")[-1]
        phrases = []
        for ph in args.get("phrases", {}).get("value", []):
            if ph.get("valueKind") == "InterpolatedStringLiteral":
                phrases.append(normalise_phrase(ph.get("value", {}).get("segments", [])))
            elif ph.get("valueKind") == "RawLiteral":
                phrases.append(ph.get("value", ""))
        sc = {
            "actionIdentifier": intent_type,
            "phraseTemplates": [{"key": ph, "alternatives": []} for ph in phrases],
            "availabilityAnnotations": availability(),
        }
        if raw_value(args.get("shortTitle")):
            sc["shortTitle"] = localizable(raw_value(args["shortTitle"]))
        if raw_value(args.get("systemImageName")):
            sc["systemImageName"] = raw_value(args["systemImageName"])
        shortcuts.append(sc)
    return shortcuts

# ---- main ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True)
    ap.add_argument("--bundle-id", required=True)
    ap.add_argument("--out", required=True, help="Metadata.appintents output dir")
    ap.add_argument("--tools-version", default="swift-6.3.3")
    ap.add_argument("--extra-bundle-id", action="append", default=[],
                    help="Other bundles (appexes) that link these intents; repeatable.")
    ap.add_argument("constvalues", nargs="+")
    a = ap.parse_args()

    entries = []
    for f in a.constvalues:
        with open(f) as fh:
            entries.extend(json.load(fh))

    bundle_ids = [a.bundle_id] + [b for b in a.extra_bundle_id if b != a.bundle_id]
    # Parameters typed as an AppEnum encode as linkEnumeration, so the enum names are needed first.
    enum_names = {e["typeName"].split(".")[-1] for e in entries if conforms(e, "AppEnum")}
    intents, entities, enums, providers, packages = [], [], [], [], []
    for e in entries:
        if conforms(e, "AppIntent"):
            intents.append(action_dict(e, a.module, bundle_ids, enum_names))
        elif conforms(e, "AppEntity"):
            entities.append(entity_dict(e, a.module))
        elif conforms(e, "AppEnum"):
            enums.append(enum_dict(e, a.module))
        elif conforms(e, "AppShortcutsProvider"):
            providers.append(e)
        elif conforms(e, "AppIntentsPackage"):
            packages.append({"identifier": e["typeName"].split(".")[-1],
                             "mangledTypeName": e["mangledTypeName"]})

    auto_shortcuts = []
    provider_mangled = ""
    if providers:
        provider_mangled = providers[0]["mangledTypeName"]
        auto_shortcuts = scan_shortcuts(providers[0])

    payload = {
        "version": ACTIONSDATA_VERSION,
        "generator": {"name": GENERATOR, "version": a.tools_version},
        "shortcutTileColor": 14,
        "actions": {act["identifier"]: act for act in intents},
        "entities": {ent["typeName"]: ent for ent in entities},
        "enums": enums,
        "queries": {},
        "autoShortcuts": auto_shortcuts,
        "autoShortcutProviderMangledName": provider_mangled,
        "negativePhrases": [],
        "assistantEntities": [],
        "assistantIntents": [],
        "assistantIntentNegativePhrases": [],
    }
    # Apple omits `packages` entirely when there are none, so only add it when non-empty.
    if packages:
        payload["packages"] = packages

    if not intents and not entities and not enums:
        print("appintents-gen: no App Intents found — no bundle written", file=sys.stderr)
        return 0

    os.makedirs(a.out, exist_ok=True)
    # sorted keys for determinism (Apple's own output is non-deterministic; we fix it)
    with open(os.path.join(a.out, "extract.actionsdata"), "w") as fh:
        json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
    with open(os.path.join(a.out, "version.json"), "w") as fh:
        json.dump({"version": VERSION_JSON, "toolsVersion": a.tools_version}, fh, sort_keys=True, indent=2)

    print(f"appintents-gen: {len(intents)} intents, {len(entities)} entities, "
          f"{len(enums)} enums, {len(auto_shortcuts)} shortcuts -> {a.out}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
