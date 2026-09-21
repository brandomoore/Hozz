"""Pure DMG packaging guards; no signing, mounting, notarization or app launch."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
beta_spec = importlib.util.spec_from_file_location("dmg_apple_beta", ROOT / "tools/lib/apple_beta.py")
beta = importlib.util.module_from_spec(beta_spec)
beta_spec.loader.exec_module(beta)
spec = importlib.util.spec_from_file_location("mac_dmg", ROOT / "tools/lib/mac_dmg.py")
dmg = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"apple_beta": beta}):
    spec.loader.exec_module(dmg)


class MacDMGTests(unittest.TestCase):
    def test_submission_requires_explicit_consent_and_exact_packaging_source(self):
        valid = ["--release-evidence", "build/evidence.json",
                 "--source-commit", "a" * 40, "--confirm-notarize"]
        self.assertEqual(dmg.arguments(valid).source_commit, "a" * 40)
        for argv in (valid[:-1], valid[:2] + ["--source-commit", "main", "--confirm-notarize"],
                     ["--source-commit", "a" * 40, "--confirm-notarize"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dmg.arguments(argv)

    def release(self, directory):
        package = directory / "original.zip"
        package.write_bytes(b"mock release package")
        return {
            "platform": "mac", "status": "succeeded", "distribution": "developer-id",
            "notarized": True, "stapled": True, "git_dirty": False,
            "validation": "developer-id-notarized-stapled-gatekeeper-verified",
            "git_commit": "a" * 40, "version": "0.1.0", "build": "1",
            "package": str(package), "package_sha256": beta.package_hash(package),
        }

    def test_input_must_be_unchanged_previously_notarized_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = self.release(directory)
            receipt = directory / "evidence.json"
            receipt.write_text(json.dumps(original))
            result, path = dmg.release_input(receipt)
            self.assertEqual(result, original)
            self.assertEqual(path, Path(original["package"]))
            for key, value in (
                ("status", "failed"), ("notarized", False), ("stapled", False),
                ("git_dirty", True), ("git_commit", "main"), ("platform", "ios"),
                ("package_sha256", "0" * 64), ("version", "../0.1.0"), ("build", "0"),
            ):
                with self.subTest(key=key):
                    receipt.write_text(json.dumps(dict(original, **{key: value})))
                    with self.assertRaises(ValueError):
                        dmg.release_input(receipt)
            receipt.write_text(json.dumps(original))
            path.write_bytes(b"unexpected replacement")
            with self.assertRaisesRegex(ValueError, "differs"):
                dmg.release_input(receipt)

    def test_app_hash_covers_bytes_modes_and_links_without_following_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Hozz.app"
            app.mkdir()
            file = app / "Hozz"
            file.write_bytes(b"executable")
            link = app / "shortcut"
            link.symlink_to("Hozz")
            original = dmg.app_digest(app)
            file.write_bytes(b"modified")
            self.assertNotEqual(dmg.app_digest(app), original)
            file.write_bytes(b"executable")
            self.assertEqual(dmg.app_digest(app), original)
            file.chmod(0o700)
            self.assertNotEqual(dmg.app_digest(app), original)
            before_link = dmg.app_digest(app)
            link.unlink()
            link.symlink_to("/nonexistent/external")
            self.assertNotEqual(dmg.app_digest(app), before_link)

    def test_installer_layout_has_large_separated_drag_targets_and_instructions(self):
        layout = dmg.finder_layout()
        self.assertFalse(layout["bwsp"]["ShowToolbar"])
        self.assertEqual(layout["icvp"]["arrangeBy"], "none")
        self.assertEqual(layout["positions"]["Hozz.app"][1], layout["positions"]["Applications"][1])
        self.assertGreater(layout["positions"]["Applications"][0] - layout["positions"]["Hozz.app"][0],
                           layout["icvp"]["iconSize"] * 2)
        self.assertIn("Drag Hozz into Applications", dmg.INSTALL_TEXT)
        self.assertIn("Eject", dmg.INSTALL_TEXT)
        self.assertIn("Open Hozz from Applications", dmg.INSTALL_TEXT)

    def test_mount_is_readonly_and_detached_when_verification_raises(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary) / "mounted"
            with patch.object(dmg.subprocess, "run") as run, \
                    patch.object(dmg.os.path, "ismount", return_value=True):
                with self.assertRaisesRegex(ValueError, "verification failed"):
                    with dmg.mounted_image(Path("installer.dmg"), mount, (5, 6)):
                        raise ValueError("verification failed")
                self.assertIn("-readonly", run.call_args_list[0].args[0])
                self.assertIn("-noautoopen", run.call_args_list[0].args[0])
                self.assertEqual(run.call_args_list[-1].args[0], ["hdiutil", "detach", str(mount)])
                self.assertNotIn("-force", run.call_args_list[-1].args[0])

    def test_failed_attachment_does_not_detach_an_unowned_volume(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(dmg.subprocess, "run", side_effect=OSError("attach failed")) as run, \
                    patch.object(dmg.os.path, "ismount", return_value=False):
                with self.assertRaises(OSError):
                    with dmg.mounted_image(Path("installer.dmg"), Path(temporary) / "mount", (5, 6)):
                        self.fail("attachment must not succeed")
                self.assertEqual(run.call_count, 1)

    def test_signed_app_must_match_source_release_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(beta, "read_plist", return_value={"CFBundleIdentifier": "wrong"}), \
                    patch.object(beta, "signed_entitlements") as sign:
                with self.assertRaisesRegex(ValueError, "CFBundleIdentifier"):
                    dmg.verify_app(Path("Hozz.app"), {"version": "0.1.0", "build": "1"},
                                   "ABCDEFGHIJ", Path(temporary), (5, 6), "fixture")
                sign.assert_not_called()


if __name__ == "__main__":
    unittest.main()
