#!/usr/bin/env python3
"""Provision signing identities + per-app profiles via the App Store Connect API.

Usage:
  provision.py identity                   # one-time: create dev cert (key stays local)
  provision.py profile <bundle-id>        # app ID + development profile incl. all devices
  provision.py dist-identity              # one-time: create DISTRIBUTION cert (for TestFlight)
  provision.py dist-profile <bundle-id>   # App Store profile (no device list) for TestFlight
  provision.py release <app.ipa>          # after ios-upload: What to Test ($TF_NOTE), public groups, beta review
Credentials are read from xtool's stored ASC key.
"""
import base64, datetime, json, os, pathlib, plistlib, re, sys, time, zipfile

import jwt, requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

HOME = pathlib.Path.home()
STORE = HOME / ".local/share/ios-ota"
PROFILES = STORE / "profiles"
API = "https://api.appstoreconnect.apple.com/v1"

creds = json.load(open(HOME / ".config/xtool/data/XTLAuthToken"))["appStoreConnect"]["_0"]


def token():
    now = int(time.time())
    return jwt.encode(
        {"iss": creds["issuerID"], "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"},
        creds["pem"], algorithm="ES256", headers={"kid": creds["id"]})


def api(method, path, ok=(), **kw):  # ok: error statuses the caller handles itself
    r = requests.request(method, API + path, headers={"Authorization": f"Bearer {token()}"}, **kw)
    if r.status_code >= 400 and r.status_code not in ok:
        sys.exit(f"ASC API {method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.text else {}


# Development signs for USB/OTA installs onto registered devices; distribution signs builds
# uploaded to App Store Connect for TestFlight. They need separate certs, profiles and entitlements.
KINDS = {
    "development": dict(cert_type="DEVELOPMENT", profile_type="IOS_APP_DEVELOPMENT",
                        key="key.pem", cert="cert.pem", cert_id="cert-id.txt",
                        prefix="ios-ota", suffix="", devices=True),
    "distribution": dict(cert_type="DISTRIBUTION", profile_type="IOS_APP_STORE",
                         key="dist-key.pem", cert="dist-cert.pem", cert_id="dist-cert-id.txt",
                         prefix="ios-testflight", suffix=".appstore", devices=False),
}


def identity(kind="development"):
    k = KINDS[kind]
    keyfile, certfile = STORE / k["key"], STORE / k["cert"]
    if keyfile.exists() and certfile.exists():
        print(f"{kind} identity exists"); return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{k['prefix']} {kind} cert")]))
           .sign(key, hashes.SHA256()))
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
    resp = api("POST", "/certificates", json={"data": {
        "type": "certificates",
        "attributes": {"certificateType": k["cert_type"], "csrContent": csr_pem}}})
    der = base64.b64decode(resp["data"]["attributes"]["certificateContent"])
    cert = x509.load_der_x509_certificate(der)
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    keyfile.chmod(0o600)
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (STORE / k["cert_id"]).write_text(resp["data"]["id"])
    print(f"created certificate {resp['data']['id']} (serial {resp['data']['attributes'].get('serialNumber')})")


def profile(bundle_id, kind="development"):
    k = KINDS[kind]
    PROFILES.mkdir(parents=True, exist_ok=True)
    cert_id = (STORE / k["cert_id"]).read_text().strip()

    bundles = api("GET", "/bundleIds", params={"filter[identifier]": bundle_id, "limit": 200})["data"]
    exact = [b for b in bundles if b["attributes"]["identifier"] == bundle_id]
    if exact:
        bid = exact[0]["id"]
    else:
        r = api("POST", "/bundleIds", json={"data": {"type": "bundleIds", "attributes": {
            "identifier": bundle_id, "name": bundle_id.replace(".", " "), "platform": "IOS"}}})
        bid = r["data"]["id"]
        print(f"registered app ID {bundle_id}")

    # An App Store profile carries no device list -- that is what makes the build installable
    # by any TestFlight tester rather than only registered devices.
    devices = [d["id"] for d in api("GET", "/devices", params={
        "filter[platform]": "IOS", "filter[status]": "ENABLED", "limit": 200})["data"]] \
        if k["devices"] else []

    # profiles can't be edited; delete stale one of the same name, then recreate
    name = f"{k['prefix']} {bundle_id}"
    for p in api("GET", "/profiles", params={"filter[name]": name, "limit": 200})["data"]:
        api("DELETE", f"/profiles/{p['id']}")
    r = api("POST", "/profiles", json={"data": {
        "type": "profiles",
        "attributes": {"name": name, "profileType": k["profile_type"]},
        "relationships": {
            "bundleId": {"data": {"type": "bundleIds", "id": bid}},
            "certificates": {"data": [{"type": "certificates", "id": cert_id}]},
            **({"devices": {"data": [{"type": "devices", "id": d} for d in devices]}}
               if k["devices"] else {}),
        }}})
    out = PROFILES / f"{bundle_id}{k['suffix']}.mobileprovision"
    out.write_bytes(base64.b64decode(r["data"]["attributes"]["profileContent"]))
    print(f"profile -> {out} ({len(devices)} devices)")


def release(ipa, note, wait_min=90):
    """Roll an uploaded build out to every tester: wait out Apple's processing, set What to Test,
    add it to the external (public) groups, submit it for beta review. Internal groups get every
    build on their own. Rerunnable: the note is overwritten, a group add is a no-op once there
    (JSON:API), and a repeat review submission is tolerated."""
    with zipfile.ZipFile(ipa) as z:
        info = plistlib.loads(z.read(next(n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n))))
    bundle, ver = info["CFBundleIdentifier"], info["CFBundleVersion"]
    app = api("GET", "/apps", params={"filter[bundleId]": bundle})["data"][0]["id"]
    print(f"==> Waiting for Apple to process build {ver}")
    for _ in range(wait_min * 2):
        b = api("GET", "/builds", params={"filter[app]": app, "filter[version]": ver})["data"]
        state = b[0]["attributes"]["processingState"] if b else "NOT_THERE_YET"
        if state == "VALID": break
        if state in ("FAILED", "INVALID"): sys.exit(f"build {ver} {state} in App Store Connect processing")
        time.sleep(30)
    else:
        sys.exit(f"build {ver} still {state} after {wait_min}m; rerun the release once it's processed")
    build = b[0]["id"]
    print(f"    processed ({build})")

    loc = [l for l in api("GET", f"/builds/{build}/betaBuildLocalizations")["data"] if l["attributes"]["locale"] == "en-US"]
    if loc:
        api("PATCH", f"/betaBuildLocalizations/{loc[0]['id']}", json={"data": {
            "type": "betaBuildLocalizations", "id": loc[0]["id"], "attributes": {"whatsNew": note}}})
    else:
        api("POST", "/betaBuildLocalizations", json={"data": {
            "type": "betaBuildLocalizations", "attributes": {"locale": "en-US", "whatsNew": note},
            "relationships": {"build": {"data": {"type": "builds", "id": build}}}}})
    print(f"==> What to Test: {note}")

    groups = [g for g in api("GET", f"/apps/{app}/betaGroups", params={"limit": 200})["data"] if not g["attributes"]["isInternalGroup"]]
    if groups:
        api("POST", f"/builds/{build}/relationships/betaGroups", json={"data": [{"type": "betaGroups", "id": g["id"]} for g in groups]})
    print("==> Added to " + (", ".join(g["attributes"]["name"] for g in groups) or "no public groups (none exist)"))

    # A build already submitted (or auto-approved) answers 409/422; what matters is where it lands.
    sub = api("POST", "/betaAppReviewSubmissions", ok=(409, 422), json={"data": {
        "type": "betaAppReviewSubmissions", "relationships": {"build": {"data": {"type": "builds", "id": build}}}}})
    ext = api("GET", f"/builds/{build}/buildBetaDetail")["data"]["attributes"]["externalBuildState"]
    print(f"==> Beta review: {ext}")
    if ext not in ("WAITING_FOR_BETA_REVIEW", "IN_BETA_REVIEW", "BETA_APPROVED", "READY_FOR_BETA_TESTING", "IN_BETA_TESTING"):
        sys.exit(f"not headed to testers: {json.dumps(sub.get('errors', sub))[:500]}")


if __name__ == "__main__":
    STORE.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "identity":
        identity()
    elif sys.argv[1] == "profile":
        profile(sys.argv[2])
    elif sys.argv[1] == "dist-identity":
        identity("distribution")
    elif sys.argv[1] == "dist-profile":
        profile(sys.argv[2], "distribution")
    elif sys.argv[1] == "release":
        release(sys.argv[2], os.environ.get("TF_NOTE", "").strip()[:4000] or "Bug fixes and improvements.")
    else:
        sys.exit(__doc__)
