#!/usr/bin/env python3
"""Hozz's explicit, local-output Apple release lane. No credential management."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = {
    "ios": ("Hozz", "generic/platform=iOS", "iphoneos", "com.thatcube.Hozz"),
    "mac": ("HozzMac", "generic/platform=macOS", "macosx", "com.thatcube.Hozz.mac"),
}
PROFILE_VARIABLES = {
    "ios": {"com.thatcube.Hozz": "HOZZ_IOS_PROFILE",
            "com.thatcube.Hozz.widget": "HOZZ_WIDGET_PROFILE"},
    "mac": {"com.thatcube.Hozz.mac": "HOZZ_MAC_PROFILE"},
}
PRIVACY_FRAMEWORKS = {
    "ios": ("HozzStore", "HozzDeliver", "HozzHealth"),
    "mac": ("HozzStore", "HozzDeliver", "HozzReceive"),
}


def arguments(argv):
    parser = argparse.ArgumentParser(
        description="Hozz Apple beta: check, compile unsigned, archive/export, upload, or notarize."
    )
    parser.add_argument("action", choices=("check", "build", "archive", "upload", "notarize"))
    parser.add_argument("platform", choices=PLATFORMS)
    parser.add_argument("--distribution", choices=("testflight", "developer-id"), default="testflight")
    parser.add_argument("--version", required=True, help="Explicit marketing version, e.g. 0.1.0")
    parser.add_argument("--build", required=True, help="Explicit integer build number, 1–9999")
    parser.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    parser.add_argument("--source-commit", help="Exact clean source commit; required for upload/notarize")
    parser.add_argument("--candidate", action="store_true",
                        help="Local signed archive probe only; non-distributable and never submitted")
    parser.add_argument("--confirm-upload", action="store_true")
    parser.add_argument("--confirm-notarize", action="store_true")
    parser.add_argument("--allow-provisioning-updates", action="store_true",
                        help="Opt in to Xcode automatic provisioning using the explicitly supplied team/API key")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", args.version):
        parser.error("--version must contain exactly three numeric components")
    if not re.fullmatch(r"[1-9][0-9]{0,3}", args.build):
        parser.error("--build must be an integer from 1 through 9999")
    if args.confirm_upload != (args.action == "upload"):
        parser.error("upload requires --confirm-upload; it is forbidden for other actions")
    if args.distribution == "developer-id" and args.platform != "mac":
        parser.error("developer-id distribution is macOS-only")
    if args.action == "upload" and args.distribution != "testflight":
        parser.error("upload is for TestFlight; use notarize for Developer ID")
    if args.confirm_notarize != (args.action == "notarize"):
        parser.error("notarize requires --confirm-notarize; it is forbidden for other actions")
    if args.action == "notarize" and args.distribution != "developer-id":
        parser.error("notarize requires --distribution developer-id")
    if args.allow_provisioning_updates and args.action == "build":
        parser.error("unsigned builds may not provision signing assets")
    if args.source_commit and not re.fullmatch(r"[a-fA-F0-9]{40}", args.source_commit):
        parser.error("--source-commit must be a full 40-character commit SHA")
    if args.action in ("upload", "notarize") and not args.source_commit:
        parser.error("upload/notarize requires --source-commit and a clean worktree")
    if args.candidate and (args.action != "archive" or args.source_commit):
        parser.error("--candidate is only for local archives and cannot carry an approved source commit")
    if args.source_commit:
        args.source_commit = args.source_commit.lower()
    return args


class SourceStateError(ValueError):
    pass


def verify_source(expected_commit):
    if not expected_commit:
        return
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if actual != expected_commit:
        raise SourceStateError("Source HEAD does not match the frozen commit; no submission permitted.")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"], cwd=ROOT, text=True)
    if dirty:
        raise SourceStateError("Source worktree is dirty; freeze and commit the intended release source before submission.")


def freeze_source(args):
    if args.candidate:
        if args.action != "archive" or args.source_commit:
            raise ValueError("A local candidate cannot be uploaded/notarized or claim an approved source commit.")
        return
    if args.action in ("archive", "upload", "notarize"):
        if not args.source_commit:
            args.source_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    verify_source(args.source_commit)


def lease_descriptors():
    required = ("OWNER", "ID", "TOKEN", "LOCK_FD", "PROOF_FD")
    env = os.environ
    if (env.get("APPLE_BUILD_LEASE_PROTOCOL") != "1"
            or env.get("APPLE_BUILD_LEASE_MODE") != "shared"
            or any(not env.get("APPLE_BUILD_LEASE_" + key) for key in required)):
        raise ValueError("Run tools/apple-beta.sh: a valid shared Apple build lease is required.")
    descriptors = tuple(int(env["APPLE_BUILD_LEASE_" + key])
                        for key in ("LOCK_FD", "PROOF_FD"))
    if min(descriptors) < 3 or descriptors[0] == descriptors[1]:
        raise ValueError("Invalid inherited build lease descriptors.")
    command = ["/usr/bin/python3", str(ROOT / "tools/lib/apple_build_lease.py"),
               "validate", "--mode", "shared", "--role-exit-code"]
    for flag, key in (("owner", "OWNER"), ("lease-id", "ID"), ("token", "TOKEN"),
                      ("lock-fd", "LOCK_FD"), ("proof-fd", "PROOF_FD")):
        command.extend(["--" + flag, env["APPLE_BUILD_LEASE_" + key]])
    result = subprocess.run(command, pass_fds=descriptors, check=False)
    if result.returncode not in (0, 10):
        raise ValueError("Inherited Apple build lease validation failed.")
    return descriptors


def signing_identity(args):
    return "Developer ID Application" if args.distribution == "developer-id" else "Apple Distribution"


def api_key_errors(env):
    errors = []
    for name, pattern in (("HOZZ_ASC_KEY_ID", r"[A-Z0-9]{10,}"),
                          ("HOZZ_ASC_ISSUER_ID", r"[A-Fa-f0-9]{8}-(?:[A-Fa-f0-9]{4}-){3}[A-Fa-f0-9]{12}")):
        if not re.fullmatch(pattern, env.get(name, "")):
            errors.append(f"Set {name} to the explicitly authorized account-level API key metadata.")
    if not env.get("HOZZ_ASC_KEY_PATH") or not Path(env["HOZZ_ASC_KEY_PATH"]).is_file():
        errors.append("HOZZ_ASC_KEY_PATH must name an existing, explicitly authorized private API key.")
    return errors


def provisioning_flags(args, env):
    if not args.allow_provisioning_updates:
        return []
    return ["-allowProvisioningUpdates", "-authenticationKeyPath", env["HOZZ_ASC_KEY_PATH"],
            "-authenticationKeyID", env["HOZZ_ASC_KEY_ID"],
            "-authenticationKeyIssuerID", env["HOZZ_ASC_ISSUER_ID"]]


def notary_auth(env):
    if env.get("HOZZ_NOTARY_KEYCHAIN_PROFILE"):
        return ["--keychain-profile", env["HOZZ_NOTARY_KEYCHAIN_PROFILE"]]
    return ["--key", env["HOZZ_ASC_KEY_PATH"], "--key-id", env["HOZZ_ASC_KEY_ID"],
            "--issuer", env["HOZZ_ASC_ISSUER_ID"]]


def prerequisites(args, env, identities):
    errors = []
    team = env.get("HOZZ_TEAM", "")
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        errors.append("Set HOZZ_TEAM to the enrolled Apple Developer Program team ID (10 characters).")
    elif (not args.allow_provisioning_updates or args.distribution == "developer-id") and not any(
        signing_identity(args) + ":" in line and f"({team})" in line
        for line in identities.splitlines()
    ):
        errors.append(f"No usable {signing_identity(args)} certificate/private key for team {team}.")
    if args.platform == "mac" and args.distribution == "testflight" and not args.allow_provisioning_updates and not any(
        "3rd Party Mac Developer Installer:" in line and f"({team})" in line
        for line in identities.splitlines()
    ):
        errors.append("Mac TestFlight requires a Mac Installer Distribution certificate/private key.")
    if args.allow_provisioning_updates:
        errors += api_key_errors(env)
    else:
        for name in PROFILE_VARIABLES[args.platform].values():
            if not env.get(name, "").strip():
                kind = "Developer ID" if args.distribution == "developer-id" else "App Store distribution"
                errors.append(f"Set {name} to an already-installed {kind} profile name/UUID.")
    if args.action == "upload":
        if not env.get("HOZZ_ASC_API_KEY_JSON"):
            errors.append("Upload requires HOZZ_ASC_API_KEY_JSON, an existing private Fastlane API key JSON file.")
        elif not Path(env["HOZZ_ASC_API_KEY_JSON"]).is_file():
            errors.append("HOZZ_ASC_API_KEY_JSON does not point to an existing file.")
    if args.action == "notarize" and not env.get("HOZZ_NOTARY_KEYCHAIN_PROFILE"):
        errors += api_key_errors(env)
    return errors


def build_command(args, directory, env):
    scheme, destination, _, _ = PLATFORMS[args.platform]
    command = [
        "xcodebuild", "-project", "Hozz.xcodeproj", "-scheme", scheme,
        "-configuration", "Release", "-destination", destination,
        "-derivedDataPath", str(directory / "DerivedData"), "-jobs", str(args.jobs),
        "-resultBundlePath", str(directory / "build.xcresult"),
        f"MARKETING_VERSION={args.version}", f"CURRENT_PROJECT_VERSION={args.build}",
        "DEBUG_INFORMATION_FORMAT=dwarf-with-dsym", "ONLY_ACTIVE_ARCH=NO",
        "HOZZ_CLINICAL_FLAG=",
    ]
    if args.action == "build":
        command += ["CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO",
                    "CODE_SIGN_IDENTITY=", "DEVELOPMENT_TEAM=", "build"]
    else:
        automatic = args.allow_provisioning_updates
        command += [f"DEVELOPMENT_TEAM={env['HOZZ_TEAM']}",
                    f"CODE_SIGN_STYLE={'Automatic' if automatic else 'Manual'}",
                    f"CODE_SIGN_IDENTITY={'Apple Development' if automatic else signing_identity(args)}"]
        command += [f"{name}={'' if automatic else env.get(name, '')}"
                    for name in PROFILE_VARIABLES[args.platform].values()]
        if args.distribution == "developer-id":
            command += ["ENABLE_HARDENED_RUNTIME=YES", "OTHER_CODE_SIGN_FLAGS=--timestamp"]
        command += provisioning_flags(args, env)
        command += ["-archivePath", str(directory / "Hozz.xcarchive"), "archive"]
    return command


def export_options(args, env, destination="export"):
    result = {
        "method": "developer-id" if args.distribution == "developer-id" else "app-store-connect",
        "destination": destination, "teamID": env["HOZZ_TEAM"],
        "signingStyle": "automatic" if args.allow_provisioning_updates else "manual",
        "manageAppVersionAndBuildNumber": False,
    }
    if not args.allow_provisioning_updates:
        result["signingCertificate"] = signing_identity(args)
        profiles = {bundle: env[name] for bundle, name in PROFILE_VARIABLES[args.platform].items()
                    if env.get(name)}
        if profiles:
            result["provisioningProfiles"] = profiles
    if args.distribution == "testflight":
        result["uploadSymbols"] = True
    if args.platform == "mac" and args.distribution == "testflight" and not args.allow_provisioning_updates:
        result["installerSigningCertificate"] = "Mac Installer Distribution"
    return result


def upload_command(args, package, env):
    return [
        "fastlane", "pilot", "upload", "--api_key_path", env["HOZZ_ASC_API_KEY_JSON"],
        "--app_identifier", PLATFORMS[args.platform][3],
        "--app_platform", "ios" if args.platform == "ios" else "osx",
        "--ipa" if args.platform == "ios" else "--pkg", str(package),
        "--skip_submission", "true", "--distribute_external", "false",
        "--notify_external_testers", "false", "--skip_waiting_for_build_processing", "false",
        "--wait_processing_timeout_duration", "3600",
    ]


def notarization_command(package, env):
    return ["xcrun", "notarytool", "submit", str(package), *notary_auth(env),
            "--wait", "--timeout", "1h", "--output-format", "json", "--no-progress"]


def package_hash(package):
    digest = hashlib.sha256()
    with package.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_plist(path):
    with path.open("rb") as source:
        return plistlib.load(source)


def check_metadata(info, bundle_id, args):
    expected = {"CFBundleIdentifier": bundle_id,
                "CFBundleShortVersionString": args.version, "CFBundleVersion": args.build}
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"{bundle_id}: {key} is {info.get(key)!r}, expected {value!r}.")


def signed_entitlements(bundle, team, identity="Apple Distribution", development_archive=False,
                        hardened=False):
    subprocess.run(["codesign", "--verify", "--strict", "--deep", str(bundle)], check=True)
    details = subprocess.run(["codesign", "-dvv", str(bundle)],
                             capture_output=True, check=True, text=True).stderr
    allowed = (identity, "Apple Development") if development_archive else (identity,)
    if f"TeamIdentifier={team}" not in details or not any(
        f"Authority={name}:" in details for name in allowed
    ):
        raise ValueError(f"{bundle.name}: not signed by the requested {identity} team.")
    if hardened and not re.search(r"flags=.*\bruntime\b", details):
        raise ValueError(f"{bundle.name}: notarization requires Hardened Runtime.")
    if hardened and not development_archive and "Timestamp=" not in details:
        raise ValueError(f"{bundle.name}: Developer ID distribution requires a secure timestamp.")
    result = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(bundle)],
                            capture_output=True, check=True)
    entitlements = plistlib.loads(result.stdout) if result.stdout.strip() else {}
    if not development_archive and (
        entitlements.get("get-task-allow") or entitlements.get("com.apple.security.get-task-allow")
    ):
        raise ValueError(f"{bundle.name}: a distributable must not have get-task-allow.")
    return entitlements


def validate_entitlements(entitlements, platform, widget=False, source=False):
    expected = {}
    if platform == "ios":
        expected["com.apple.security.application-groups"] = ["group.com.thatcube.Hozz"]
        if not widget:
            expected.update({"com.apple.developer.healthkit": True,
                             "com.apple.developer.healthkit.background-delivery": True})
            if entitlements.get("com.apple.developer.healthkit.access", []):
                raise ValueError("Clinical Health Records are not enabled by this beta lane.")
    else:
        expected.update({"com.apple.security.app-sandbox": True,
                         "com.apple.security.network.server": True,
                         "com.apple.security.network.client": True,
                         "com.apple.security.files.user-selected.read-write": True})
    for key, value in expected.items():
        if entitlements.get(key) != value:
            raise ValueError(f"Missing or unexpected entitlement: {key}")
    if not widget and not any(
        value.endswith(".com.thatcube.Hozz.shared")
        or (source and value == "$(AppIdentifierPrefix)com.thatcube.Hozz.shared")
        for value in entitlements.get("keychain-access-groups", [])
    ):
        raise ValueError("Missing shared Hozz Keychain access group.")


def validate_health_purpose_strings(info):
    for key in ("NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription"):
        value = info.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"iOS artifact is missing required HealthKit purpose string: {key}")


def validate_artifact(app, args, directory, env, exported=False):
    mac = args.platform == "mac"
    resources = app / "Contents/Resources" if mac else app
    info = read_plist(app / "Contents/Info.plist" if mac else app / "Info.plist")
    check_metadata(info, PLATFORMS[args.platform][3], args)
    if not mac:
        validate_health_purpose_strings(info)
    if not mac and info.get("UIDeviceFamily") != [1, 2]:
        raise ValueError("iOS artifact must support both iPhone and iPad.")
    if info.get("ITSAppUsesNonExemptEncryption") is not False:
        raise ValueError("Missing encryption declaration.")
    if not (resources / "PrivacyInfo.xcprivacy").is_file():
        raise ValueError("App privacy manifest is missing.")
    frameworks = app / "Contents/Frameworks" if mac else app / "Frameworks"
    for name in PRIVACY_FRAMEWORKS[args.platform]:
        framework = frameworks / f"{name}.framework"
        manifest = framework / "Resources/PrivacyInfo.xcprivacy" if mac else framework / "PrivacyInfo.xcprivacy"
        if not manifest.is_file():
            raise ValueError(f"{name}: required-reason privacy manifest is missing.")
    widget = app / "PlugIns/HozzWidget.appex"
    if not mac:
        check_metadata(read_plist(widget / "Info.plist"), "com.thatcube.Hozz.widget", args)
        if not (widget / "PrivacyInfo.xcprivacy").is_file():
            raise ValueError("Widget privacy manifest is missing.")
    signed = args.action != "build"
    if signed:
        signature_options = {"identity": signing_identity(args),
                             "development_archive": args.allow_provisioning_updates and not exported,
                             "hardened": args.distribution == "developer-id"}
        entitlements = signed_entitlements(app, env["HOZZ_TEAM"], **signature_options)
        validate_entitlements(entitlements, args.platform)
        (directory / "app-entitlements.plist").write_bytes(plistlib.dumps(entitlements))
        if not mac:
            validate_entitlements(signed_entitlements(widget, env["HOZZ_TEAM"], **signature_options),
                                  "ios", widget=True)
        else:
            helper = app / "Contents/MacOS/hozz-mcp"
            helper_entitlements = signed_entitlements(helper, env["HOZZ_TEAM"], **signature_options)
            if args.distribution == "testflight" and not helper_entitlements.get("com.apple.security.app-sandbox"):
                raise ValueError("Mac App Store blocker: embedded hozz-mcp is not sandboxed. "
                                 "Resolve its archive-access design before TestFlight distribution.")
            if args.distribution == "developer-id" and helper_entitlements.get("com.apple.security.app-sandbox"):
                raise ValueError("Developer ID packaging must preserve the MCP tool's independent archive access.")
    else:
        source = ROOT / ("Mac/HozzMac.entitlements" if mac else "App/Hozz.entitlements")
        validate_entitlements(read_plist(source), args.platform, source=True)
        if not mac:
            validate_entitlements(read_plist(ROOT / "Widget/HozzWidget.entitlements"), "ios", widget=True)
    dsym_root = (directory / "Hozz.xcarchive/dSYMs" if signed
                 else directory / "DerivedData/Build/Products" / f"Release{'-iphoneos' if not mac else ''}")
    if not (dsym_root / "Hozz.app.dSYM").is_dir():
        raise ValueError("App dSYM is missing; retain symbols with every beta.")
    binary = app / "Contents/MacOS/Hozz" if mac else app / "Hozz"
    binary_uuids = subprocess.check_output(["xcrun", "dwarfdump", "--uuid", str(binary)], text=True)
    dsym_uuids = subprocess.check_output(
        ["xcrun", "dwarfdump", "--uuid", str(dsym_root / "Hozz.app.dSYM")], text=True)
    pattern = r"UUID: ([A-Fa-f0-9-]+) \(([^)]+)\)"
    if not re.findall(pattern, binary_uuids) or set(re.findall(pattern, binary_uuids)) != set(re.findall(pattern, dsym_uuids)):
        raise ValueError("App and dSYM UUIDs do not match.")
    validation = "signed-archive" if signed else "unsigned-compile-only"
    if signed and args.allow_provisioning_updates and not exported:
        validation = "development-signed-archive-not-distributable"
    return {"app": str(app), "dsyms": str(dsym_root), "uuids": binary_uuids.splitlines(),
            "signed": signed, "validation": validation}


def run_logged(command, logfile, descriptors, env=None, source_commit=None):
    print(f"Running {command[0]} ({logfile})", flush=True)
    with logfile.open("wb") as output:
        verify_source(source_commit)
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
                                pass_fds=descriptors, env=env, check=False)
    if result.returncode:
        raise ValueError(f"{command[0]} failed ({result.returncode}); inspect {logfile}.")


def check_notary_result(report, exit_code):
    if not isinstance(report, dict):
        raise ValueError("Notarization returned an unexpected report; inspect its raw logs.")
    submission_id = report.get("id")
    try:
        uuid.UUID(submission_id)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Notarization returned no valid submission ID; inspect its raw logs.")
    if exit_code or report.get("status") != "Accepted":
        raise ValueError(f"Notarization is not accepted ({report.get('status', 'unknown')}); "
                         f"submission {submission_id}. Do not distribute or blindly resubmit.")


def notarize(args, app, package, directory, env, descriptors, evidence):
    verify_source(args.source_commit)
    evidence["notarization_attempted"] = True
    evidence["notarized"] = None
    evidence_file = directory / "evidence.json"
    evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
    report_path = directory / "notary-submission.json"
    with report_path.open("wb") as output, (directory / "notary-submission.stderr.log").open("wb") as errors:
        verify_source(args.source_commit)
        result = subprocess.run(notarization_command(package, env), cwd=ROOT, stdout=output,
                                stderr=errors, pass_fds=descriptors, check=False)
    try:
        report = json.loads(report_path.read_text())
    except (ValueError, UnicodeError):
        raise ValueError(f"Notarization did not return JSON; inspect {report_path} and its stderr log.")
    if not isinstance(report, dict):
        raise ValueError(f"Notarization returned an unexpected report; inspect {report_path}.")
    submission_id = report.get("id")
    evidence["notary_submission_id"] = submission_id
    evidence["notary_status"] = report.get("status")
    evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
    if isinstance(submission_id, str) and re.fullmatch(r"[A-Fa-f0-9-]{36}", submission_id):
        try:
            run_logged(["xcrun", "notarytool", "log", submission_id, *notary_auth(env),
                        str(directory / "notary-service-log.json")],
                       directory / "notary-log-fetch.log", descriptors)
        except ValueError as error:
            evidence["notary_log_fetch_error"] = str(error)
    check_notary_result(report, result.returncode)
    evidence["notarized"] = True
    evidence["validation"] = "notary-accepted-stapling-pending"
    evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
    run_logged(["xcrun", "stapler", "staple", str(app)], directory / "staple.log", descriptors)
    run_logged(["xcrun", "stapler", "validate", str(app)], directory / "staple-validation.log", descriptors)
    run_logged(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)],
               directory / "gatekeeper.log", descriptors)
    validate_artifact(app, args, directory, env, exported=True)
    final = directory / f"Hozz-{args.version}-{args.build}-mac.zip"
    run_logged(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(final)],
               directory / "stapled-package.log", descriptors)
    inspection = directory / "stapled-package-inspection"
    run_logged(["ditto", "-x", "-k", str(final), str(inspection)],
               directory / "stapled-package-extraction.log", descriptors)
    recovered_app = inspection / "Hozz.app"
    run_logged(["xcrun", "stapler", "validate", str(recovered_app)],
               directory / "stapled-package-validation.log", descriptors)
    validate_artifact(recovered_app, args, directory, env, exported=True)
    evidence.update({"stapled": True, "package": str(final), "package_sha256": package_hash(final),
                     "validation": "developer-id-notarized-stapled-gatekeeper-verified"})


def run(args):
    descriptors = lease_descriptors()
    freeze_source(args)
    if not (ROOT / "Local.xcconfig").is_file():
        raise ValueError("Local.xcconfig is missing. Create it locally; it may be empty for unsigned builds.")
    env = os.environ
    if args.action == "upload" and not shutil.which("fastlane"):
        raise ValueError("Upload requires fastlane (tested with 2.238.0); install it before retrying.")
    if args.action != "build":
        identities = subprocess.check_output(["security", "find-identity", "-v"], text=True)
        errors = prerequisites(args, env, identities)
        if errors:
            raise ValueError("Signing preflight failed:\n- " + "\n- ".join(errors))
        if args.action == "check":
            print("Local signing inputs present. Actual profile/signing compatibility requires archive/export. "
                  "No account, provisioning, upload, or notarization request was made.")
            return
    directory = ROOT / "build/apple-beta" / (
        f"{args.platform}-{args.distribution}-{args.version}-{args.build}-{args.action}-{uuid.uuid4().hex[:12]}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    print(f"Evidence: {directory}", flush=True)
    evidence = {"platform": args.platform, "version": args.version, "build": args.build,
                "action": args.action, "status": "started", "testflight_uploaded": False,
                "upload_attempted": False, "processing_verified": False, "distribution_requested": False,
                "distribution": args.distribution, "automatic_provisioning_authorized": args.allow_provisioning_updates,
                "notarization_attempted": False, "notarized": False, "stapled": False,
                "local_candidate": args.candidate,
                "eligible_for_apple_submission": False,
                "expected_source_commit": args.source_commit,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "git_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"]))}
    evidence_file = directory / "evidence.json"
    evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
    try:
        run_logged(["xcodebuild", "-version"], directory / "xcode-version.log", descriptors)
        run_logged([str(ROOT / "tools/generate-project.sh")], directory / "generation.log", descriptors)
        verify_source(args.source_commit)
        command = build_command(args, directory, env)
        evidence["build_command"] = command
        run_logged(command, directory / "build.log", descriptors)
        verify_source(args.source_commit)
        if args.action == "build":
            product = "Release-iphoneos" if args.platform == "ios" else "Release"
            app = directory / "DerivedData/Build/Products" / product / "Hozz.app"
        else:
            app = directory / "Hozz.xcarchive/Products/Applications/Hozz.app"
        evidence.update(validate_artifact(app, args, directory, env))
        if args.action != "build":
            options = directory / "ExportOptions.plist"
            options.write_bytes(plistlib.dumps(export_options(args, env)))
            export = ["xcodebuild", "-exportArchive", "-archivePath", str(directory / "Hozz.xcarchive"),
                      "-exportPath", str(directory / "export"), "-exportOptionsPlist", str(options),
                      *provisioning_flags(args, env)]
            run_logged(export, directory / "export.log", descriptors)
            verify_source(args.source_commit)
            if args.distribution == "developer-id":
                exported_app = directory / "export/Hozz.app"
                validate_artifact(exported_app, args, directory, env, exported=True)
                package = directory / f"Hozz-{args.version}-{args.build}-mac-unnotarized.zip"
                run_logged(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                            str(exported_app), str(package)], directory / "package.log", descriptors)
                evidence["exported_app"] = str(exported_app)
            else:
                extension = "*.ipa" if args.platform == "ios" else "*.pkg"
                packages = list((directory / "export").glob(extension))
                if len(packages) != 1:
                    raise ValueError(f"Expected exactly one {extension} in the export.")
                package = packages[0]
            inspection = directory / "export-inspection"
            if args.distribution == "testflight" and args.platform == "ios":
                run_logged(["ditto", "-x", "-k", str(package), str(inspection)],
                           directory / "export-inspection.log", descriptors)
                exported_app = inspection / "Payload/Hozz.app"
            elif args.distribution == "testflight":
                run_logged(["pkgutil", "--check-signature", str(package)],
                           directory / "package-signature.log", descriptors)
                signature = (directory / "package-signature.log").read_text()
                if (f"({env['HOZZ_TEAM']})" not in signature
                        or "3rd Party Mac Developer Installer:" not in signature):
                    raise ValueError("Exported installer is not signed by the requested distribution team.")
                run_logged(["pkgutil", "--expand-full", str(package), str(inspection)],
                           directory / "export-inspection.log", descriptors)
                apps = list(inspection.rglob("Hozz.app"))
                if len(apps) != 1:
                    raise ValueError("Expected one Hozz.app in the exported installer.")
                exported_app = apps[0]
            validate_artifact(exported_app, args, directory, env, exported=True)
            evidence["package"] = str(package)
            evidence["package_sha256"] = package_hash(package)
            evidence["validation"] = ("developer-id-signed-export-not-notarized"
                                      if args.distribution == "developer-id" else "signed-export-local-only")
            if args.action == "notarize":
                evidence["notarization_input"] = str(package)
                notarize(args, exported_app, package, directory, env, descriptors, evidence)
            if args.action == "upload":
                verify_source(args.source_commit)
                upload_env = dict(env, FASTLANE_SKIP_UPDATE_CHECK="1", FASTLANE_HIDE_CHANGELOG="1",
                                  FASTLANE_OPT_OUT_USAGE="1", CI="1")
                evidence["upload_attempted"] = True
                evidence["testflight_uploaded"] = None
                evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
                run_logged(upload_command(args, package, env), directory / "upload.log",
                           descriptors, upload_env, source_commit=args.source_commit)
                evidence["testflight_uploaded"] = True
                evidence["processing_verified"] = True
                evidence["validation"] = "testflight-processed-no-distribution-requested"
        verify_source(args.source_commit)
        if args.candidate:
            evidence["validation"] = "signed-candidate-non-distributable"
        evidence["eligible_for_apple_submission"] = args.action != "build" and not args.candidate
        evidence["status"] = "succeeded"
        print(f"{evidence['validation']}: {directory}", flush=True)
    except Exception as error:
        if isinstance(error, SourceStateError):
            if evidence["testflight_uploaded"] is None:
                evidence["testflight_uploaded"] = False
                evidence["upload_attempted"] = False
            if evidence["notarized"] is None:
                evidence["notarized"] = False
                evidence["notarization_attempted"] = False
        evidence["status"] = "failed"
        evidence["error"] = str(error)
        raise
    finally:
        evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    try:
        mode = sys.argv[1]
        args = arguments(sys.argv[2:])
        if mode == "run":
            run(args)
        elif mode != "validate":
            raise ValueError("Use tools/apple-beta.sh.")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
