#!/usr/bin/env python3
"""Read-only App Store Connect inventory; JWTs and raw API bodies never reach disk."""

import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from apple_beta import ROOT, api_key_errors, lease_descriptors

API = "https://api.appstoreconnect.apple.com"
BUNDLES = ("com.thatcube.Hozz", "com.thatcube.Hozz.widget", "com.thatcube.Hozz.mac")
APP_BUNDLES = ("com.thatcube.Hozz", "com.thatcube.Hozz.mac")


def arguments(argv):
    parser = argparse.ArgumentParser(
        description="Read-only exact Hozz app/bundle/certificate/profile metadata; never creates records."
    )
    parser.add_argument("action", choices=("inspect",))
    parser.add_argument("--team", required=True, help="Expected team, also checked against Local.xcconfig")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Z0-9]{10}", args.team):
        parser.error("--team must be a 10-character Apple Developer team identifier")
    return args


def base64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def raw_ecdsa_signature(der):
    if len(der) < 8 or der[0] != 0x30 or der[1] != len(der) - 2:
        raise ValueError("Unexpected ES256 signature encoding.")
    values, position = [], 2
    for _ in range(2):
        if position + 2 > len(der) or der[position] != 0x02:
            raise ValueError("Unexpected ES256 signature integer.")
        length = der[position + 1]
        start, end = position + 2, position + 2 + length
        integer = der[start:end].lstrip(b"\0")
        if not length or end > len(der) or len(integer) > 32:
            raise ValueError("Unexpected ES256 signature size.")
        values.append(integer.rjust(32, b"\0"))
        position = end
    if position != len(der):
        raise ValueError("Unexpected trailing signature data.")
    return b"".join(values)


def token(env):
    now = int(time.time())
    header = {"alg": "ES256", "kid": env["HOZZ_ASC_KEY_ID"], "typ": "JWT"}
    payload = {"iss": env["HOZZ_ASC_ISSUER_ID"], "iat": now, "exp": now + 600,
               "aud": "appstoreconnect-v1"}
    message = ".".join(base64url(json.dumps(value, separators=(",", ":")).encode())
                       for value in (header, payload)).encode()
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", env["HOZZ_ASC_KEY_PATH"], "-passin", "pass:"],
        input=message, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ValueError("JWT signing failed; verify the authorized key path, format, and access permissions.")
    return message.decode() + "." + base64url(raw_ecdsa_signature(result.stdout))


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def safe_codes(body):
    try:
        errors = json.loads(body).get("errors", [])
        return [item["code"] for item in errors if isinstance(item.get("code"), str)
                and re.fullmatch(r"[A-Z0-9_.-]{1,100}", item["code"])]
    except (ValueError, AttributeError, TypeError, KeyError):
        return []


class ReadOnlyClient:
    def __init__(self, bearer):
        self.bearer = bearer
        self.opener = urllib.request.build_opener(NoRedirects())
        self.requests = []

    def get(self, path, parameters=None):
        if not path.startswith("/v1/") or "://" in path:
            raise ValueError("Only App Store Connect v1 paths are allowed.")
        url = API + path
        if parameters:
            url += "?" + urllib.parse.urlencode(parameters)
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + self.bearer, "Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                value = json.load(response)
                self.requests.append({"path": path, "http_status": response.status})
                return value
        except urllib.error.HTTPError as error:
            codes = safe_codes(error.read())
            self.requests.append({"path": path, "http_status": error.code, "error_codes": codes})
            raise ValueError(f"GET {path}: HTTP {error.code}; codes={','.join(codes) or 'unavailable'}")
        except (urllib.error.URLError, ValueError) as error:
            self.requests.append({"path": path, "failure": type(error).__name__})
            raise ValueError(f"GET {path}: request/response failure; no response body was logged.")

    def all(self, path, parameters=None):
        values = []
        for _ in range(20):
            response = self.get(path, parameters)
            values += response.get("data", [])
            following = response.get("links", {}).get("next")
            if not following:
                return values
            parsed = urllib.parse.urlparse(following)
            if parsed.scheme != "https" or parsed.netloc != "api.appstoreconnect.apple.com":
                raise ValueError("Refusing an unexpected pagination origin.")
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            parameters = None
        raise ValueError("Pagination exceeded the bounded inventory; do not treat it as complete.")


def selected(resource, fields):
    attributes = resource.get("attributes", {})
    return {"id": resource.get("id"), "type": resource.get("type"),
            **{field: attributes[field] for field in fields if field in attributes}}


def certificate(resource, local_fingerprints):
    result = selected(resource, ("name", "certificateType", "displayName", "expirationDate", "serialNumber"))
    content = resource.get("attributes", {}).get("certificateContent")
    if content:
        try:
            fingerprint = hashlib.sha1(base64.b64decode(content, validate=True)).hexdigest().upper()
            result["local_private_key_identity_present"] = fingerprint in local_fingerprints
        except ValueError:
            result["local_private_key_identity_present"] = None
    return result


def collect(client, local_fingerprints):
    result = {"apps": {}, "recent_builds": {}, "bundle_ids": {}, "certificates": [], "query_failures": []}

    def attempt(label, operation):
        try:
            return operation()
        except ValueError as error:
            result["query_failures"].append({"resource": label, "error": str(error)})
            return None

    for bundle in APP_BUNDLES:
        resources = attempt(f"apps/{bundle}", lambda: client.all("/v1/apps", {"filter[bundleId]": bundle, "limit": 200}))
        result["apps"][bundle] = None if resources is None else [
            selected(item, ("name", "bundleId", "sku", "primaryLocale"))
            for item in resources if item.get("attributes", {}).get("bundleId") == bundle
        ]
        for app in result["apps"][bundle] or []:
            builds = attempt(f"builds/{bundle}", lambda: client.get(
                "/v1/builds", {"filter[app]": app["id"], "limit": 10, "sort": "-uploadedDate"}).get("data", []))
            result["recent_builds"][bundle] = None if builds is None else [
                selected(item, ("version", "processingState", "uploadedDate", "expired"))
                for item in builds
            ][:10]
    for bundle in BUNDLES:
        resources = attempt(f"bundleIds/{bundle}", lambda: client.all(
            "/v1/bundleIds", {"filter[identifier]": bundle, "limit": 200}))
        if resources is None:
            result["bundle_ids"][bundle] = None
            continue
        entries = []
        for resource in resources:
            if resource.get("attributes", {}).get("identifier") != bundle:
                continue
            entry = selected(resource, ("name", "identifier", "platform", "seedId"))
            identifier = resource["id"]
            capabilities = attempt(f"capabilities/{bundle}", lambda: client.all(
                f"/v1/bundleIds/{identifier}/bundleIdCapabilities"))
            entry["capabilities"] = None if capabilities is None else [
                selected(item, ("capabilityType", "settings")) for item in capabilities
            ]
            profiles = attempt(f"profiles/{bundle}", lambda: client.all(
                f"/v1/bundleIds/{identifier}/profiles", {"limit": 200}))
            entry["profiles"] = None if profiles is None else [
                selected(item, ("name", "profileType", "profileState", "uuid", "expirationDate", "platform"))
                for item in profiles
            ]
            entries.append(entry)
        result["bundle_ids"][bundle] = entries
    resources = attempt("certificates", lambda: client.all("/v1/certificates", {"limit": 200}))
    result["certificates"] = None if resources is None else [
        certificate(item, local_fingerprints) for item in resources
    ]
    return result


def unexpired(resource):
    try:
        expiration = datetime.datetime.fromisoformat(resource["expirationDate"].replace("Z", "+00:00"))
        return expiration > datetime.datetime.now(datetime.timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False


def proposed_changes(inventory, team):
    """A proposal only: this module has no mutating HTTP method."""
    if inventory["query_failures"]:
        return {"ready_for_review": False, "reason": "Inventory is incomplete; do not infer missing records."}
    bundle_ids = inventory["bundle_ids"]
    for bundle in BUNDLES:
        entries = bundle_ids.get(bundle)
        if not entries or len(entries) != 1 or entries[0].get("seedId") != team:
            return {"ready_for_review": False,
                    "reason": "Every exact Hozz bundle must already have one verified team-matching record."}
    distribution = [item for item in inventory["certificates"]
                    if item.get("certificateType") in ("DISTRIBUTION", "IOS_DISTRIBUTION")
                    and item.get("local_private_key_identity_present") and unexpired(item)]
    developer_id = [item for item in inventory["certificates"]
                    if item.get("certificateType") in ("DEVELOPER_ID_APPLICATION", "DEVELOPER_ID_APPLICATION_G2")
                    and item.get("local_private_key_identity_present") and unexpired(item)]
    profiles = []
    for bundle, profile_type, name, certificates in (
        ("com.thatcube.Hozz", "IOS_APP_STORE", "Hozz iOS App Store", distribution),
        ("com.thatcube.Hozz.widget", "IOS_APP_STORE", "Hozz Widget App Store", distribution),
        ("com.thatcube.Hozz.mac", "MAC_APP_DIRECT", "Hozz Developer ID", developer_id),
    ):
        entry = bundle_ids[bundle][0]
        active = [profile["id"] for profile in entry["profiles"]
                  if profile.get("profileType") == profile_type and profile.get("profileState") == "ACTIVE"
                  and unexpired(profile)]
        if active:
            profiles.append({"bundle_id": bundle, "existing_active_candidates": active,
                             "action": "Validate profile entitlements/certificate compatibility before reuse."})
            continue
        proposal = {"bundle_id": bundle, "profile_type": profile_type, "method": "POST", "path": "/v1/profiles",
                    "requires_confirmation": True}
        if len(certificates) != 1:
            proposal["blocked_by"] = "Select one compatible signing certificate with a usable local private key, or authorize Xcode-managed signing."
        else:
            proposal["proposed_body"] = {
                "data": {"type": "profiles", "attributes": {"name": name, "profileType": profile_type},
                         "relationships": {
                             "bundleId": {"data": {"type": "bundleIds", "id": entry["id"]}},
                             "certificates": {"data": [{"type": "certificates", "id": certificates[0]["id"]}]},
                         }}
            }
        profiles.append(proposal)
    return {
        "ready_for_review": True, "cloud_mutations_performed": False,
        "ios_app_record": "Already exists; no creation." if inventory["apps"].get(APP_BUNDLES[0])
                          else "Create on the App Store Connect website after confirmation; the public API cannot create apps.",
        "mac_app_record": "Not required for Developer ID direct distribution; do not create.",
        "distribution_signing": "A matching local distribution identity is available." if distribution else
            "No matching local Apple Distribution identity is API-visible. Authorization is needed for Xcode to create/download distribution signing assets.",
        "profiles": profiles,
        "automatic_alternative": {
            "requires_confirmation": True,
            "entrypoint": "tools/apple-beta.sh archive <platform> --version <version> --build <build> --allow-provisioning-updates",
            "mac_additional_option": "--distribution developer-id",
            "scope": "Existing team and exact Hozz bundle IDs only. Xcode may create/refresh development profiles for archives and distribution profiles/assets for exports.",
        },
        "excluded_actions": ["No certificate revocation or profile deletion", "No bundle identifier or team changes",
                             "No capability toggles requested by this proposal", "No app-record edits",
                             "No upload, notarization submission, tester invitations, distribution, or publication"],
        "app_creation_documentation": "https://developer.apple.com/documentation/appstoreconnectapi/apps",
    }


def run(args):
    lease_descriptors()
    errors = api_key_errors(os.environ)
    if errors:
        raise ValueError("\n".join(errors))
    local = ROOT / "Local.xcconfig"
    if not local.is_file():
        raise ValueError("Local.xcconfig is required to verify the selected team.")
    teams = re.findall(r"(?m)^\s*DEVELOPMENT_TEAM\s*=\s*([A-Z0-9]{10})\s*(?://.*)?$", local.read_text())
    if teams != [args.team]:
        raise ValueError("Explicit team does not unambiguously match Local.xcconfig; no account request made.")
    identities = subprocess.check_output(["security", "find-identity", "-v"], text=True)
    fingerprints = set(re.findall(r"\b[A-F0-9]{40}\b", identities))
    client = ReadOnlyClient(token(os.environ))
    result = collect(client, fingerprints)
    result["proposed_changes"] = proposed_changes(result, args.team)
    result.update({"queried_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "expected_local_team": args.team, "key_id": os.environ["HOZZ_ASC_KEY_ID"],
                   "cloud_mutations_performed": False, "requests": client.requests})
    directory = ROOT / "build/apple-account" / uuid.uuid4().hex[:12]
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "readiness.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(f"Sanitized read-only account evidence: {path}")
    print(f"Requests: {len(client.requests)}; resource query failures: {len(result['query_failures'])}. No cloud mutations.")
    if result["query_failures"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        mode = sys.argv[1]
        args = arguments(sys.argv[2:])
        if mode == "run":
            sys.exit(run(args))
        elif mode != "validate":
            raise ValueError("Use tools/apple-account.sh.")
    except (ValueError, OSError, subprocess.CalledProcessError):
        sys.exit("Apple account inspection failed. Verify authorized credential metadata/local tools; no secrets were logged.")
