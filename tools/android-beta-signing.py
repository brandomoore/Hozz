#!/usr/bin/env python3
"""Owner-authorized macOS Keychain setup/reuse for Hozz APK signing."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = Path.home() / ".config/hozz/signing"
KEY_NAME = "hozz-android-release.p12"
METADATA_NAME = "release.json"
SERVICE = "com.thatcube.hozz.android-release"
ACCOUNT = "release-keystore"
ALIAS = "hozz-android-release"
PASSWORD_ENV = "HOZZ_ANDROID_SETUP_PASSWORD"


class SigningError(Exception):
    pass


def call(command, *, env=None, input=None):
    # Never emit captured output: Keychain stdout and keytool errors may contain secrets.
    return subprocess.run(command, input=input, env=env, capture_output=True,
                          check=False, timeout=120)


def keychain_find(*, password=False, keychain=None):
    command = ["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT]
    if password:
        command.append("-w")
    if keychain:
        command.append(keychain)
    return call(command)


def require_absent_keychain():
    result = keychain_find()
    if result.returncode != 44:  # errSecItemNotFound; all other states require owner review.
        raise SigningError("Exact release Keychain account exists or could not be checked; not overwriting.")


def require_private(path, mode, directory=False):
    if path.is_symlink() or not path.exists():
        raise SigningError("Signing storage is missing or is a symlink; owner review required.")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
        raise SigningError("Signing storage must be owner-controlled with directory 0700 / files 0600.")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise SigningError("Unexpected signing storage type; owner review required.")


def keytool():
    home = Path(os.environ.get(
        "JAVA_HOME", "/Applications/Android Studio.app/Contents/jbr/Contents/Home",
    ))
    executable = home / "bin/keytool"
    if not executable.is_file():
        raise SigningError("Set JAVA_HOME to the verified JDK; keytool is unavailable.")
    return str(executable)


def fingerprint(key, password):
    env = dict(os.environ, **{PASSWORD_ENV: password})
    result = call([
        keytool(), "-exportcert", "-alias", ALIAS, "-keystore", str(key),
        "-storetype", "PKCS12", "-storepass:env", PASSWORD_ENV,
    ], env=env)
    if result.returncode != 0 or not result.stdout:
        raise SigningError("Release key could not be verified against its Keychain password.")
    return hashlib.sha256(result.stdout).hexdigest()


def store_password(password, keychain):
    command = " ".join(shlex.quote(value) for value in [
        "add-generic-password", "-s", SERVICE, "-a", ACCOUNT,
        "-l", "Hozz Android release signing", "-w", password, keychain,
    ])
    result = call(["/usr/bin/security", "-i"], input=(command + "\n").encode())
    restored = keychain_find(password=True, keychain=keychain)
    if result.returncode or restored.returncode or restored.stdout.rstrip(b"\n") != password.encode():
        raise SigningError("Keychain storage verification failed; retained setup requires owner review.")


def setup():
    if DIRECTORY.resolve().is_relative_to(ROOT.resolve()):
        raise SigningError("Dedicated signing storage must resolve outside the repository.")
    require_absent_keychain()
    # Writes use a path relative to the checkout, resolving only to the authorized external directory.
    directory = Path(os.path.relpath(DIRECTORY, Path.cwd()))
    if directory.exists():
        require_private(directory, 0o700, directory=True)
        if any(directory.iterdir()):
            raise SigningError("Dedicated signing directory is not empty; no key or setup state overwritten.")
    else:
        directory.mkdir(mode=0o700, parents=True)
    marker = directory / ".setup-in-progress"
    with marker.open("x") as output:
        output.write("Owner review required if setup is interrupted. Do not regenerate the key.\n")
    candidate = directory / (".pending-" + uuid.uuid4().hex + ".p12")
    password = secrets.token_urlsafe(48)
    env = dict(os.environ, **{PASSWORD_ENV: password})
    result = call([
        keytool(), "-genkeypair", "-alias", ALIAS, "-keyalg", "RSA", "-keysize", "4096",
        "-sigalg", "SHA256withRSA", "-validity", "10000",
        "-dname", "CN=Hozz Android Release", "-storetype", "PKCS12",
        "-keystore", str(candidate), "-storepass:env", PASSWORD_ENV,
        "-keypass:env", PASSWORD_ENV, "-noprompt",
    ], env=env)
    if result.returncode:
        raise SigningError("Release key generation failed; setup state retained for owner review.")
    candidate.chmod(0o600)
    digest = fingerprint(candidate, password)
    require_absent_keychain()
    default = call(["/usr/bin/security", "default-keychain", "-d", "user"])
    if default.returncode:
        raise SigningError("Default user Keychain unavailable; setup state retained.")
    keychain = default.stdout.decode().strip().strip('"')
    if not Path(keychain).is_file() or "\n" in keychain:
        raise SigningError("Default user Keychain path is invalid; setup state retained.")
    store_password(password, keychain)
    # Hard-link promotion is atomic and refuses to replace any existing release key.
    os.link(candidate, directory / KEY_NAME)
    metadata = {
        "keystore": str(DIRECTORY / KEY_NAME), "alias": ALIAS,
        "keychainService": SERVICE, "keychainAccount": ACCOUNT, "keychain": keychain,
        "certificateSha256": digest, "storeType": "PKCS12",
        "keyAlgorithm": "RSA", "keyBits": 4096,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    with (directory / METADATA_NAME).open("x") as output:
        json.dump(metadata, output, indent=2)
        output.write("\n")
    candidate.unlink()
    marker.unlink()
    print_public(metadata)
    print("BACKUP REQUIRED BEFORE PUBLICATION: preserve the encrypted keystore and its password "
          "in separate owner-controlled recoverable backups. Losing either prevents future updates.")


def load():
    if DIRECTORY.resolve().is_relative_to(ROOT.resolve()):
        raise SigningError("Dedicated signing storage must resolve outside the repository.")
    require_private(DIRECTORY, 0o700, directory=True)
    if (DIRECTORY / ".setup-in-progress").exists() or list(DIRECTORY.glob(".pending-*.p12")):
        raise SigningError("Incomplete signing setup detected; owner review required, no automatic repair.")
    key = DIRECTORY / KEY_NAME
    metadata_path = DIRECTORY / METADATA_NAME
    require_private(key, 0o600)
    require_private(metadata_path, 0o600)
    metadata = json.loads(metadata_path.read_text())
    expected = {
        "keystore": str(key), "alias": ALIAS, "keychainService": SERVICE,
        "keychainAccount": ACCOUNT, "storeType": "PKCS12",
    }
    if any(metadata.get(name) != value for name, value in expected.items()):
        raise SigningError("Release signing metadata does not match its dedicated identity.")
    keychain = metadata.get("keychain")
    if not isinstance(keychain, str) or not Path(keychain).is_file():
        raise SigningError("Recorded release Keychain is unavailable.")
    restored = keychain_find(password=True, keychain=keychain)
    if restored.returncode or not restored.stdout:
        raise SigningError("Release Keychain password unavailable; no fallback key will be used.")
    password = restored.stdout.rstrip(b"\n").decode()
    if fingerprint(key, password) != metadata.get("certificateSha256"):
        raise SigningError("Release certificate differs from its pinned identity; refusing to sign.")
    return metadata, password


def print_public(metadata):
    print("Release keystore:", metadata["keystore"])
    print("Certificate SHA-256:", metadata["certificateSha256"])
    print("Keychain service/account:", SERVICE, "/", ACCOUNT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup_parser = subparsers.add_parser("setup", help="Create only after explicit owner authorization.")
    setup_parser.add_argument("--owner-authorized", action="store_true", required=True)
    subparsers.add_parser("status", help="Verify existing key, Keychain password and pinned certificate.")
    package_parser = subparsers.add_parser("package", help="Reread Keychain and run signed APK packaging.")
    package_parser.add_argument("--version-name", required=True)
    package_parser.add_argument("--version-code", required=True, type=int)
    package_parser.add_argument("--output", required=True)
    package_parser.add_argument("--candidate", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if sys.platform != "darwin":
            raise SigningError("This wrapper requires macOS Keychain; unsigned CI does not use it.")
        if args.command == "setup":
            setup()
            return 0
        metadata, password = load()
        if args.command == "status":
            print_public(metadata)
            return 0
        env = dict(os.environ, **{
            "HOZZ_ANDROID_KEYSTORE": metadata["keystore"],
            "HOZZ_ANDROID_KEY_ALIAS": metadata["alias"],
            "HOZZ_ANDROID_STORE_PASSWORD": password,
            "HOZZ_ANDROID_KEY_PASSWORD": password,
            "HOZZ_ANDROID_CERT_SHA256": metadata["certificateSha256"],
        })
        command = [
            sys.executable, str(ROOT / "tools/android-beta.py"),
            "--version-name", args.version_name, "--version-code", str(args.version_code),
            "--output", args.output,
        ]
        if args.candidate:
            command.append("--candidate")
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    except (SigningError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        # Exception strings from subprocess/JSON can contain captured credentials; emit only our errors.
        detail = str(error) if isinstance(error, SigningError) else "Operation failed; owner review required."
        print("Android release signing stopped: " + detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
