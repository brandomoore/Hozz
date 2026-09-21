#!/usr/bin/env python3
"""Make a notarized drag-to-Applications DMG from an already notarized Hozz ZIP."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import uuid

import apple_beta as beta


INSTALL_TEXT = """Install Hozz

1. Drag Hozz into Applications.
2. Eject the Hozz disk image.
3. Open Hozz from Applications.

Requires macOS 14 or newer. No installer or administrator script runs.
If updating, quit Hozz first and keep your existing health archive.
If macOS reports a security problem, do not bypass the warning.

Beta instructions and feedback:
https://github.com/brandomoore/Hozz/releases/tag/v0.1.0-beta.1
"""


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-evidence", type=Path, required=True,
                        help="Successful notarized Mac ZIP evidence.json from apple-beta.sh")
    parser.add_argument("--source-commit", required=True,
                        help="Exact clean commit of this packaging tool, not the older app")
    parser.add_argument("--confirm-notarize", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-f0-9]{40}", args.source_commit):
        parser.error("--source-commit must be a full lowercase commit SHA")
    return args


def release_input(path):
    evidence = json.loads(path.read_text())
    expected = {
        "platform": "mac", "status": "succeeded", "distribution": "developer-id",
        "notarized": True, "stapled": True, "git_dirty": False,
        "validation": "developer-id-notarized-stapled-gatekeeper-verified",
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError("Input must be a successful, clean-source notarized Mac release.")
    source = evidence.get("git_commit", "")
    digest = evidence.get("package_sha256", "")
    if not re.fullmatch(r"[a-f0-9]{40}", source) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Release input is missing its source commit or package hash.")
    version, build = evidence.get("version", ""), evidence.get("build", "")
    if (not re.fullmatch(r"\d+\.\d+\.\d+", version)
            or not re.fullmatch(r"[1-9]\d{0,3}", build)):
        raise ValueError("Release input has an invalid version or build.")
    package = Path(evidence.get("package", ""))
    if not package.is_file() or package.suffix != ".zip" or beta.package_hash(package) != digest:
        raise ValueError("Notarized source ZIP is missing or differs from its release hash.")
    return evidence, package


def app_digest(app):
    """Compare actual app contents, modes and symlinks without following framework links."""
    entries = []
    for root, directories, files in os.walk(app, followlinks=False):
        for name in sorted(directories + files):
            path = Path(root) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                kind, value = "link", os.readlink(path)
            elif stat.S_ISDIR(mode):
                kind, value = "directory", ""
            elif stat.S_ISREG(mode):
                kind, value = "file", beta.package_hash(path)
            else:
                raise ValueError(f"Unsupported app entry: {path.relative_to(app)}")
            entries.append((str(path.relative_to(app)), stat.S_IMODE(mode), kind, value))
    return hashlib.sha256(json.dumps(sorted(entries), separators=(",", ":")).encode()).hexdigest()


def finder_layout():
    return {
        "bwsp": {
            "ShowStatusBar": False, "ShowPathbar": False, "ShowToolbar": False,
            "ShowSidebar": False, "ShowTabView": False,
            "WindowBounds": "{{240, 180}, {640, 360}}",
        },
        "icvp": {
            "viewOptionsVersion": 1, "backgroundType": 0, "iconSize": 112.0,
            "textSize": 14.0, "gridSpacing": 100.0, "gridOffsetX": 0.0,
            "gridOffsetY": 0.0, "arrangeBy": "none",
            "showIconPreview": True, "showItemInfo": False, "labelOnBottom": True,
        },
        "positions": {"Hozz.app": (170, 135), "Applications": (470, 135),
                      "How to install.txt": (320, 280)},
    }


def write_layout(path):
    from ds_store import DSStore
    layout = finder_layout()
    with DSStore.open(str(path), "w+") as store:
        store["."]["bwsp"] = layout["bwsp"]
        store["."]["icvp"] = layout["icvp"]
        store["."]["vstl"] = ("type", "icnv")
        for name, position in layout["positions"].items():
            store[name]["Iloc"] = position


def validate_layout(path):
    from ds_store import DSStore
    expected = finder_layout()
    with DSStore.open(str(path), "r") as store:
        if store["."]["bwsp"] != expected["bwsp"] or store["."]["icvp"] != expected["icvp"]:
            raise ValueError("DMG Finder window settings changed.")
        for name, position in expected["positions"].items():
            if tuple(store[name]["Iloc"]) != position:
                raise ValueError(f"DMG installer icon position changed: {name}")


@contextmanager
def mounted_image(image, mountpoint, descriptors):
    mountpoint.mkdir()
    try:
        subprocess.run(
            ["hdiutil", "attach", str(image), "-readonly", "-nobrowse", "-noautoopen",
             "-mountpoint", str(mountpoint)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, pass_fds=descriptors,
        )
        if not os.path.ismount(mountpoint):
            raise ValueError("hdiutil did not mount the DMG at its private verification path.")
        yield mountpoint
    finally:
        if os.path.ismount(mountpoint):
            # Never eject by volume name or force-detach another process's files.
            subprocess.run(["hdiutil", "detach", str(mountpoint)],
                           check=True, pass_fds=descriptors)


def verify_app(app, release, team, directory, descriptors, prefix):
    info = beta.read_plist(app / "Contents/Info.plist")
    for key, value in {
        "CFBundleIdentifier": "com.thatcube.Hozz.mac",
        "CFBundleShortVersionString": release["version"],
        "CFBundleVersion": release["build"],
    }.items():
        if info.get(key) != value:
            raise ValueError(f"Input app has unexpected {key}.")
    entitlements = beta.signed_entitlements(app, team, "Developer ID Application", hardened=True)
    beta.validate_entitlements(entitlements, "mac")
    beta.run_logged(["xcrun", "stapler", "validate", str(app)],
                    directory / f"{prefix}-staple.log", descriptors)
    beta.run_logged(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app)],
                    directory / f"{prefix}-gatekeeper.log", descriptors)


def run(args):
    descriptors = beta.lease_descriptors()
    beta.verify_source(args.source_commit)
    release, package = release_input(args.release_evidence)
    team = os.environ.get("HOZZ_TEAM", "")
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise ValueError("Set HOZZ_TEAM to the release app's Developer ID team.")
    if not os.environ.get("HOZZ_NOTARY_KEYCHAIN_PROFILE"):
        errors = beta.api_key_errors(os.environ)
        if errors:
            raise ValueError("; ".join(errors))
    try:
        import ds_store  # noqa: F401
    except ImportError as error:
        raise ValueError("Install tools/requirements-dmg.txt in a venv; set HOZZ_DMG_PYTHON.") from error
    identities = subprocess.check_output(["security", "find-identity", "-v", "-p", "codesigning"], text=True)
    matches = re.findall(r'([A-Fa-f0-9]{40}) "Developer ID Application: [^"\n]+ \('
                         + re.escape(team) + r'\)"', identities)
    if len(matches) != 1:
        raise ValueError("Expected exactly one usable Developer ID Application identity for HOZZ_TEAM.")
    directory = beta.ROOT / "build/mac-dmg" / f"{release['version']}-{release['build']}-{uuid.uuid4().hex[:12]}"
    directory.mkdir(parents=True)
    evidence_path = directory / "evidence.json"
    evidence = {
        "status": "started", "app_source_commit": release["git_commit"],
        "packaging_source_commit": args.source_commit, "input_zip_sha256": release["package_sha256"],
        "version": release["version"], "build": release["build"],
        "notarization_attempted": False,
        "notarized": False, "stapled": False, "distributable": False,
    }

    def save():
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")

    def logged(command, name):
        beta.run_logged(command, directory / f"{name}.log", descriptors,
                        source_commit=args.source_commit)

    save()
    print(f"Evidence: {evidence_path}", flush=True)
    try:
        staging = directory / "contents"
        logged(["ditto", "-x", "-k", str(package), str(staging)], "extract-original")
        if {p.name for p in staging.iterdir()} != {"Hozz.app"}:
            raise ValueError("Source ZIP must contain only the notarized Hozz.app.")
        app = staging / "Hozz.app"
        verify_app(app, release, team, directory, descriptors, "original")
        original_digest = app_digest(app)
        evidence["app_contents_sha256"] = original_digest
        (staging / "Applications").symlink_to("/Applications", target_is_directory=True)
        (staging / "How to install.txt").write_text(INSTALL_TEXT)
        write_layout(staging / ".DS_Store")
        validate_layout(staging / ".DS_Store")
        icon = app / "Contents/Resources/AppIcon.icns"
        if not icon.is_file():
            raise ValueError("The signed app's existing volume icon is missing.")
        shutil.copyfile(icon, staging / ".VolumeIcon.icns")
        logged(["SetFile", "-a", "C", str(staging)], "volume-icon")
        image = directory / f"Hozz-{release['version']}-{release['build']}-mac.dmg"
        logged(["hdiutil", "create", "-srcfolder", str(staging), "-volname",
                "Hozz - Drag to Applications", "-fs", "HFS+", "-format", "UDZO",
                "-imagekey", "zlib-level=9", str(image)], "create-image")
        logged(["codesign", "--sign", matches[0], "--timestamp", str(image)], "sign-image")
        logged(["codesign", "--verify", "--strict", str(image)], "verify-image-signature")
        beta.verify_source(args.source_commit)
        evidence["notarization_attempted"] = True
        save()
        logged(beta.notarization_command(image, os.environ), "notarization")
        report = json.loads((directory / "notarization.log").read_text())
        evidence["notary_submission_id"] = report.get("id")
        evidence["notary_status"] = report.get("status")
        save()
        beta.check_notary_result(report, 0)
        evidence["notarized"] = True
        save()
        logged(["xcrun", "stapler", "staple", str(image)], "staple-image")
        logged(["xcrun", "stapler", "validate", str(image)], "verify-image-ticket")
        logged(["codesign", "--verify", "--strict", str(image)], "verify-stapled-signature")
        logged(["spctl", "--assess", "--type", "open", "--context",
                "context:primary-signature", "--verbose=2", str(image)], "image-gatekeeper")
        logged(["hdiutil", "verify", str(image)], "verify-image-integrity")
        with mounted_image(image, directory / "mounted", descriptors) as mounted:
            if os.readlink(mounted / "Applications") != "/Applications":
                raise ValueError("Installer Applications shortcut points to the wrong location.")
            validate_layout(mounted / ".DS_Store")
            if (mounted / "How to install.txt").read_text() != INSTALL_TEXT:
                raise ValueError("Installer instructions are missing or changed.")
            if app_digest(mounted / "Hozz.app") != original_digest:
                raise ValueError("DMG changed the notarized application's contents.")
            # Exercise drag-copy semantics into private test storage, never /Applications.
            copied = directory / "copy-test/Hozz.app"
            logged(["ditto", str(mounted / "Hozz.app"), str(copied)], "verify-install-copy")
            if app_digest(copied) != original_digest:
                raise ValueError("Copied app differs from the original release.")
            verify_app(copied, release, team, directory, descriptors, "copied")
        beta.verify_source(args.source_commit)
        digest = beta.package_hash(image)
        (directory / "SHA256SUMS").write_text(f"{digest}  {image.name}\n")
        evidence.update({
            "status": "succeeded", "package": str(image), "package_sha256": digest,
            "stapled": True, "distributable": True, "app_bytes_preserved": True,
            "drag_copy_verified": True, "installed_or_launched": False,
        })
        print(f"Verified notarized installer: {image}", flush=True)
    except Exception as error:
        evidence["error"] = str(error)
        evidence["status"] = "failed"
        raise
    finally:
        save()


if __name__ == "__main__":
    try:
        args = arguments(sys.argv[2:])
        if sys.argv[1] == "run":
            run(args)
        elif sys.argv[1] != "validate":
            raise ValueError("Use tools/mac-dmg.sh.")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
