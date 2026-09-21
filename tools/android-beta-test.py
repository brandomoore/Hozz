#!/usr/bin/env python3
"""Packaging guards; uses no keystores, passwords, SDK changes, or network."""

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location("android_beta", Path(__file__).with_name("android-beta.py"))
beta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beta)
signing_spec = importlib.util.spec_from_file_location(
    "android_beta_signing", Path(__file__).with_name("android-beta-signing.py"),
)
signing = importlib.util.module_from_spec(signing_spec)
signing_spec.loader.exec_module(signing)


class BetaPackagingTests(unittest.TestCase):
    def test_only_explicit_beta_versions_and_android_version_codes(self):
        beta.validate_version("0.1.0-beta.1", 10001)
        for name, code in [("0.1.0", 1), ("../beta", 1), ("0.1.0-beta.1", 0),
                           ("0.1.0-beta.1", 2_100_000_001)]:
            with self.assertRaises(beta.PackagingError):
                beta.validate_version(name, code)

    def test_no_signing_inputs_never_uses_debug_key(self):
        with self.assertRaisesRegex(beta.PackagingError, "No debug-key fallback"):
            beta.validate_signing({})

    def test_repository_keystore_is_rejected_even_when_it_exists(self):
        env = {key: "not-a-secret-fixture" for key in beta.SIGNING_INPUTS}
        env["HOZZ_ANDROID_KEYSTORE"] = str(beta.ROOT / "fixture.jks")
        with patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(beta.PackagingError, "outside this repository"):
                beta.validate_signing(env)

    def test_invalid_certificate_fingerprint_is_rejected(self):
        env = {key: "invalid" for key in beta.SIGNING_INPUTS}
        env["HOZZ_ANDROID_KEYSTORE"] = "/owner-controlled-release-key.jks"
        with patch.object(Path, "is_file", return_value=True):
            with self.assertRaisesRegex(beta.PackagingError, "64 hex digits"):
                beta.validate_signing(env)

    def test_signature_mismatch_missing_multiple_and_debug_signers_fail_closed(self):
        expected = "a" * 64
        report = f"Signer #1 certificate SHA-256 digest: {expected}\n"
        beta.verify_certificate(report, expected)
        for invalid in ["", report.replace(expected, "b" * 64),
                        report + report.replace("#1", "#2"), report + "CN=Android Debug"]:
            with self.assertRaises(beta.PackagingError):
                beta.verify_certificate(invalid, expected)

    def test_release_manifest_rejects_wrong_identity_version_debug_or_permissions(self):
        badging = (
            "package: name='com.thatcube.hozz' versionCode='10001' versionName='0.1.0-beta.1'\n"
            "sdkVersion:'28'\ntargetSdkVersion:'36'\n"
            "application-icon-160:'res/IG.xml'\n" +
            "".join(f"uses-permission: name='android.permission.health.WRITE_{kind}'\n"
                    for kind in beta.WRITE_TYPES)
        )
        manifest = (
            "A: android:icon(0x01010002)=@0x7f010000\n"
            "A: android:roundIcon(0x0101052c)=@0x7f010001\n"
        )
        beta.verify_manifest(badging, manifest, "0.1.0-beta.1", 10001)
        beta.verify_manifest(badging.replace("sdkVersion:", "minSdkVersion:"),
                             manifest, "0.1.0-beta.1", 10001)
        for invalid in [
            badging.replace("com.thatcube.hozz", "com.example.hozz"),
            badging.replace("10001", "10002"),
            badging + "application-debuggable\n",
            badging + "uses-permission: name='android.permission.health.READ_WEIGHT'\n",
            badging + "uses-permission: name='android.permission.INTERNET'\n",
            badging.replace("sdkVersion:'28'", "sdkVersion:'34'"),
            badging.replace("application-icon-160:", "no-icon:"),
        ]:
            with self.assertRaises(beta.PackagingError):
                beta.verify_manifest(invalid, manifest, "0.1.0-beta.1", 10001)
        for invalid in [
            manifest + "HozzTestActivity",
            manifest + "android:debuggable(0x101000f)=(type 0x12)0xffffffff",
            "",
            manifest.replace("android:roundIcon", "android:missing"),
        ]:
            with self.assertRaises(beta.PackagingError):
                beta.verify_manifest(badging, invalid, "0.1.0-beta.1", 10001)

    def test_signing_tool_failure_does_not_expose_tool_output(self):
        result = subprocess.CompletedProcess([], 1, stdout="sensitive fixture", stderr="key fixture")
        with patch.object(subprocess, "run", return_value=result):
            with self.assertRaisesRegex(beta.PackagingError, r"^Release signing failed \(exit 1\)\.$"):
                beta.run_checked(["apksigner", "sign"], sensitive=True)

    def test_invalid_output_stops_before_build(self):
        args = type("Args", (), {
            "version_name": "0.1.0-beta.1", "version_code": 10001,
            "unsigned": True, "output": "../outside",
        })()
        with patch.object(subprocess, "run") as process:
            with self.assertRaises(beta.PackagingError):
                beta.package(args)
            process.assert_not_called()

    def test_distribution_requires_clean_unchanged_commit(self):
        for dirty, initial in [(" M tracked-file", None), ("?? new-file", None), ("", "old")]:
            with patch.object(beta, "run_checked", side_effect=["current\n", dirty]):
                with self.assertRaisesRegex(beta.PackagingError, "clean committed tree"):
                    beta.verify_source_state(True, False, initial)
        with patch.object(beta, "run_checked", side_effect=["current\n", ""]):
            self.assertEqual(("current", False),
                             beta.verify_source_state(True, False, "current"))

    def test_explicit_candidate_and_unsigned_can_verify_dirty_tree(self):
        for signed, candidate in [(True, True), (False, False)]:
            with patch.object(beta, "run_checked", side_effect=["current\n", " M file"]):
                self.assertEqual(("current", True),
                                 beta.verify_source_state(signed, candidate))

    def test_setup_refuses_existing_or_ambiguous_keychain_account(self):
        for status in [0, 1, 36]:
            with patch.object(signing, "keychain_find", return_value=SimpleNamespace(returncode=status)):
                with self.assertRaises(signing.SigningError):
                    signing.require_absent_keychain()
        with patch.object(signing, "keychain_find", return_value=SimpleNamespace(returncode=44)):
            signing.require_absent_keychain()

    def test_keytool_password_is_environment_only(self):
        result = subprocess.CompletedProcess([], 0, stdout=b"public certificate", stderr=b"")
        with patch.object(signing, "keytool", return_value="keytool"), \
                patch.object(signing, "call", return_value=result) as call:
            signing.fingerprint(Path("fake.p12"), "not-a-real-password")
            command = call.call_args.args[0]
            self.assertNotIn("not-a-real-password", command)
            self.assertIn("-storepass:env", command)
            self.assertEqual("not-a-real-password",
                             call.call_args.kwargs["env"][signing.PASSWORD_ENV])

    def test_keychain_password_is_stdin_only_and_interactive_mode_ends_at_eof(self):
        added = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        restored = subprocess.CompletedProcess([], 0, stdout=b"not-a-real-password\n", stderr=b"")
        with patch.object(signing, "call", return_value=added) as call, \
                patch.object(signing, "keychain_find", return_value=restored):
            signing.store_password("not-a-real-password", "/owner/login.keychain-db")
            self.assertEqual(["/usr/bin/security", "-i"], call.call_args.args[0])
            input_bytes = call.call_args.kwargs["input"]
            self.assertIn(b"-w not-a-real-password", input_bytes)
            self.assertTrue(input_bytes.endswith(b"\n"))
            self.assertNotIn(b"\nquit", input_bytes)

    def test_keychain_write_requires_exact_private_readback(self):
        added = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        restored = subprocess.CompletedProcess([], 0, stdout=b"wrong-secret\n", stderr=b"")
        with patch.object(signing, "call", return_value=added), \
                patch.object(signing, "keychain_find", return_value=restored):
            with self.assertRaisesRegex(signing.SigningError, "^Keychain storage verification failed;"):
                signing.store_password("not-a-real-password", "/owner/login.keychain-db")

    def test_signing_files_require_private_regular_owner_files(self):
        path = Mock()
        path.is_symlink.return_value = False
        path.exists.return_value = True
        uid = signing.os.getuid()
        path.stat.return_value = SimpleNamespace(st_uid=uid, st_mode=0o100600)
        signing.require_private(path, 0o600)
        for owner, mode in [(uid, 0o100644), (uid + 1, 0o100600), (uid, 0o010600)]:
            path.stat.return_value = SimpleNamespace(st_uid=owner, st_mode=mode)
            with self.assertRaises(signing.SigningError):
                signing.require_private(path, 0o600)

    def test_launcher_assets_preserve_existing_brand_provenance_and_adaptive_layers(self):
        root = beta.ROOT
        metadata = json.loads((root / "Android/launcher-icon-provenance.json").read_text())
        self.assertEqual(
            hashlib.sha256((root / metadata["source"]).read_bytes()).hexdigest(),
            metadata["sourceSha256"],
        )
        self.assertEqual(3, len(metadata["generatedSha256"]))
        for path, digest in metadata["generatedSha256"].items():
            data = (root / path).read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        android = "{http://schemas.android.com/apk/res/android}"
        resources = root / "Android/app/src/main/res"
        for name in ("ic_launcher", "ic_launcher_round"):
            icon = ET.parse(resources / f"mipmap-anydpi/{name}.xml").getroot()
            self.assertEqual("adaptive-icon", icon.tag)
            self.assertEqual("@drawable/hozz_launcher_background",
                             icon.find("background").get(android + "drawable"))
            self.assertEqual("@drawable/hozz_launcher_foreground",
                             icon.find("foreground").get(android + "drawable"))
            self.assertEqual("@drawable/hozz_launcher_monochrome",
                             icon.find("monochrome").get(android + "drawable"))


if __name__ == "__main__":
    unittest.main()
