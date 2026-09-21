#!/usr/bin/env python3
"""Build and verify a sideloadable preview; never create or substitute a signing key."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
APP_ID = "com.thatcube.hozz"
SIGNING_INPUTS = (
    "HOZZ_ANDROID_KEYSTORE",
    "HOZZ_ANDROID_KEY_ALIAS",
    "HOZZ_ANDROID_STORE_PASSWORD",
    "HOZZ_ANDROID_KEY_PASSWORD",
    "HOZZ_ANDROID_CERT_SHA256",
)
WRITE_TYPES = {
    "HEART_RATE", "WEIGHT", "HEIGHT", "EXERCISE",
    "STEPS", "DISTANCE", "ACTIVE_CALORIES_BURNED", "SLEEP",
}


class PackagingError(Exception):
    pass


def validate_version(name, code):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+", name):
        raise PackagingError("Version name must be major.minor.patch-beta.number.")
    if not 1 <= code <= 2_100_000_000:
        raise PackagingError("Version code must be between 1 and 2100000000.")


def validate_signing(env, root=ROOT):
    if any(not env.get(name) for name in SIGNING_INPUTS):
        raise PackagingError(
            "Release signing inputs missing. Supply all HOZZ_ANDROID signing "
            "variables documented in docs/android-beta.md, or use --unsigned "
            "for a NON-DISTRIBUTABLE build. No debug-key fallback is available."
        )
    key = Path(env["HOZZ_ANDROID_KEYSTORE"]).expanduser()
    if not key.is_absolute() or not key.is_file():
        raise PackagingError("Release keystore must be an existing external absolute path.")
    if key.resolve().is_relative_to(root.resolve()):
        raise PackagingError("Keep the release keystore outside this repository.")
    fingerprint = env["HOZZ_ANDROID_CERT_SHA256"].replace(":", "").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise PackagingError("Expected release certificate SHA-256 must have 64 hex digits.")
    return key, fingerprint


def verify_manifest(badging, manifest, version_name, version_code):
    package = re.search(
        r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'",
        badging, re.MULTILINE,
    )
    if not package or package.groups() != (APP_ID, str(version_code), version_name):
        raise PackagingError("APK application ID or version does not match the requested beta.")
    if "application-debuggable" in badging or re.search(
        r"android:debuggable.*(?:0xffffffff|true)", manifest,
    ):
        raise PackagingError("Refusing a debuggable APK.")
    if "HozzTestActivity" in manifest or "android.permission.INTERNET" in badging:
        raise PackagingError("Unexpected test activity or network permission in release APK.")
    if not re.search(r"^(?:sdkVersion|minSdkVersion):'28'$", badging, re.MULTILINE) or \
            "targetSdkVersion:'36'" not in badging:
        raise PackagingError("Unexpected Android compatibility levels.")
    permissions = set(re.findall(r"uses-permission: name='([^']+)'", badging))
    health = {p for p in permissions if p.startswith("android.permission.health.")}
    expected = {"android.permission.health.WRITE_" + item for item in WRITE_TYPES}
    if health != expected:
        raise PackagingError("Release Health Connect permissions differ from the write-only contract.")
    for attribute in ("icon", "roundIcon"):
        if not re.search(rf"android:{attribute}\([^)]*\)=@0x[1-9a-fA-F][0-9a-fA-F]*", manifest):
            raise PackagingError("Release APK must declare launcher and round icon resources.")
    if not re.search(r"application-icon-\d+:'res/[^']+\.xml'", badging):
        raise PackagingError("Release APK launcher icon did not resolve to its bundled resource.")


def verify_certificate(report, expected):
    certificates = re.findall(
        r"^Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]+)$",
        report, re.MULTILINE,
    )
    if [value.lower() for value in certificates] != [expected]:
        raise PackagingError("APK signer does not match the owner-pinned release certificate.")
    if "Android Debug" in report:
        raise PackagingError("A debug certificate cannot sign a downloadable beta.")


def run_checked(command, *, env=None, log=None, sensitive=False):
    if log:
        with log.open("w") as output:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            raise PackagingError(f"Build failed; inspect {log.relative_to(ROOT)}.")
        return ""
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, check=False)
    if result.returncode:
        # Signing tool diagnostics can echo key material inputs; never persist them.
        label = "Release signing" if sensitive else Path(command[0]).name
        raise PackagingError(f"{label} failed (exit {result.returncode}).")
    return result.stdout + result.stderr


def verify_source_state(signing, candidate, initial_commit=None):
    commit = run_checked(["git", "rev-parse", "HEAD"]).strip()
    dirty = bool(run_checked(["git", "status", "--porcelain"]).strip())
    if signing and not candidate and (dirty or initial_commit not in (None, commit)):
        raise PackagingError(
            "Distribution signing requires an unchanged clean committed tree. "
            "Commit release changes first, or explicitly use --candidate "
            "for a NON-DISTRIBUTABLE signed check."
        )
    return commit, dirty


def package(args):
    validate_version(args.version_name, args.version_code)
    candidate_build = getattr(args, "candidate", False)
    if candidate_build and args.unsigned:
        raise PackagingError("--candidate is for signed checks; do not combine it with --unsigned.")
    signing = None if args.unsigned else validate_signing(os.environ)
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise PackagingError("Artifact output must be a new relative directory inside this checkout.")
    output = (ROOT / output).resolve()
    if not output.is_relative_to(ROOT) or output == ROOT or output.exists():
        raise PackagingError("Artifact directory must be inside this checkout and must not exist.")
    sdk = Path(os.environ.get("ANDROID_HOME", os.environ.get("ANDROID_SDK_ROOT", "")))
    tools = sdk / "build-tools" / "36.0.0"
    for name in ("aapt2", "zipalign", "apksigner"):
        if not (tools / name).is_file():
            raise PackagingError("Set ANDROID_HOME to an SDK with build-tools 36.0.0 installed.")
    if not os.environ.get("JAVA_HOME"):
        raise PackagingError("Set JAVA_HOME to the Android Studio bundled JDK.")
    initial_commit, initial_dirty = verify_source_state(bool(signing), candidate_build)
    output.mkdir(parents=True)
    work = output / "verification-work"
    work.mkdir()
    build_env = {key: value for key, value in os.environ.items() if key not in SIGNING_INPUTS}
    run_checked([
        str(ROOT / "Android/gradlew"), "-p", str(ROOT / "Android"),
        "--no-daemon", "--no-parallel", "--max-workers=1",
        "--no-configuration-cache", "--console=plain",
        "-Dorg.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8",
        "-Pkotlin.compiler.execution.strategy=in-process",
        f"-PhozzVersionName={args.version_name}",
        f"-PhozzVersionCode={args.version_code}", ":app:assembleRelease",
    ], env=build_env, log=output / "gradle.log")
    source = ROOT / "Android/app/build/outputs/apk/release/app-release-unsigned.apk"
    if not source.is_file():
        raise PackagingError("Expected Gradle unsigned release output is missing.")
    aligned = work / "aligned.apk"
    run_checked([str(tools / "zipalign"), "-P", "16", "-f", "4", str(source), str(aligned)])
    suffix = (
        "UNSIGNED-NOT-FOR-DISTRIBUTION" if args.unsigned else
        "signed-CANDIDATE-NOT-FOR-DISTRIBUTION" if candidate_build else "signed"
    )
    filename = f"hozz-{args.version_name}-{args.version_code}-{suffix}.apk"
    candidate = work / filename
    if signing:
        key, fingerprint = signing
        run_checked([
            str(tools / "apksigner"), "sign", "--ks", str(key),
            "--ks-key-alias", os.environ["HOZZ_ANDROID_KEY_ALIAS"],
            "--ks-pass", "env:HOZZ_ANDROID_STORE_PASSWORD",
            "--key-pass", "env:HOZZ_ANDROID_KEY_PASSWORD",
            "--v4-signing-enabled", "false", "--out", str(candidate), str(aligned),
        ], sensitive=True)
        report = run_checked([
            str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(candidate),
        ])
        verify_certificate(report, fingerprint)
        (output / "signature.txt").write_text("\n".join(
            line for line in report.splitlines()
            if line.startswith("Verified using") or "certificate SHA-256 digest:" in line
        ) + "\n")
    else:
        shutil.copyfile(aligned, candidate)
        fingerprint = None
        result = subprocess.run([str(tools / "apksigner"), "verify", str(candidate)],
                                capture_output=True, check=False)
        if result.returncode == 0:
            raise PackagingError("Unsigned verification unexpectedly found a signed APK.")
        (output / "signature.txt").write_text("UNSIGNED — NOT DISTRIBUTABLE OR INSTALLABLE\n")
    run_checked([str(tools / "zipalign"), "-c", "-P", "16", "4", str(candidate)])
    badging = run_checked([str(tools / "aapt2"), "dump", "badging", str(candidate)])
    manifest = run_checked([
        str(tools / "aapt2"), "dump", "xmltree", str(candidate),
        "--file", "AndroidManifest.xml",
    ])
    verify_manifest(badging, manifest, args.version_name, args.version_code)
    launcher_resources = sorted(set(re.findall(r"application-icon-\d+:'([^']+)'", badging)))
    with zipfile.ZipFile(candidate) as apk:
        members = {item.filename: item.file_size for item in apk.infolist()}
        if any(members.get(path, 0) == 0 for path in launcher_resources):
            raise PackagingError("Resolved launcher icon resources are missing or empty in the APK.")
    (output / "manifest.txt").write_text(manifest)
    (output / "badging.txt").write_text(badging)
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    commit, dirty = verify_source_state(bool(signing), candidate_build, initial_commit)
    distributable = bool(signing) and not candidate_build
    metadata = {
        "applicationId": APP_ID, "versionName": args.version_name,
        "versionCode": args.version_code, "minSdk": 28, "targetSdk": 36,
        "debuggable": False, "distributable": distributable,
        "signedCandidate": candidate_build,
        "launcherIconResources": launcher_resources,
        "certificateSha256": fingerprint, "apkSha256": digest, "apk": filename,
        "gitCommit": commit, "workingTreeDirty": initial_dirty or dirty,
        "experimentalHealthConnect": "Explicit opt-in on API 34+; no Google Play approval claimed",
    }
    (output / "beta.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(f"{digest}  {filename}\n")
    candidate.rename(output / filename)
    aligned.unlink()
    work.rmdir()
    label = "SIGNED BETA" if distributable else (
        "SIGNED CANDIDATE / NOT DISTRIBUTABLE" if signing else "UNSIGNED / NOT DISTRIBUTABLE"
    )
    print(f"Verified {label}: "
          f"{output.relative_to(ROOT) / filename}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--version-code", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--unsigned", action="store_true",
                        help="Verify packaging only; produces a non-distributable APK.")
    parser.add_argument("--candidate", action="store_true",
                        help="Allow dirty-tree signing only as an explicitly non-distributable candidate.")
    args = parser.parse_args()
    try:
        package(args)
    except (PackagingError, OSError) as error:
        print(f"Android beta packaging stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
