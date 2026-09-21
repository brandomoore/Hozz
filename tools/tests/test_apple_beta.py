#!/usr/bin/env python3
"""Pure packaging checks: no Xcode, signing, simulator, network, or lease writes."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import shutil
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("apple_beta", ROOT / "tools/lib/apple_beta.py")
beta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beta)


class AppleBetaTests(unittest.TestCase):
    def args(self, action="build", platform="ios", *extra):
        source = ["--source-commit", "a" * 40] if action in ("upload", "notarize") else []
        return beta.arguments([action, platform, "--version", "0.1.0", "--build", "42", *source, *extra])

    def environment(self):
        return {"HOZZ_TEAM": "ABCDEFGHIJ", "HOZZ_IOS_PROFILE": "Hozz App Store",
                "HOZZ_WIDGET_PROFILE": "Hozz Widget App Store", "HOZZ_MAC_PROFILE": "Hozz Mac Store",
                "HOZZ_ASC_API_KEY_JSON": "/private/credentials/existing-key.json",
                "HOZZ_ASC_KEY_PATH": "/private/credentials/existing-key.p8",
                "HOZZ_ASC_KEY_ID": "ABC1234567",
                "HOZZ_ASC_ISSUER_ID": "12345678-abcd-1234-abcd-123456789abc"}

    def test_invalid_arguments_fail_before_writes(self):
        invalid = [
            [], ["archive", "android", "--version", "0.1.0", "--build", "1"],
            ["build", "ios", "--version", "1.2", "--build", "1"],
            ["build", "ios", "--version", "../1.0", "--build", "1"],
            ["build", "ios", "--version", "1.0.0", "--build", "0"],
            ["build", "ios", "--version", "1.0.0", "--build", "10000"],
            ["build", "ios", "--version", "1.0.0", "--build", "-1"],
            ["build", "ios", "--version", "1.0.0", "--build", "1", "--jobs", "0"],
            ["upload", "ios", "--version", "1.0.0", "--build", "1"],
            ["archive", "mac", "--version", "1.0.0", "--build", "1", "--confirm-upload"],
        ]
        for argv in invalid:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    beta.arguments(argv)

    def test_unsigned_command_never_archives_provisions_installs_or_launches(self):
        for platform in beta.PLATFORMS:
            args = self.args(platform=platform)
            command = beta.build_command(args, Path("build/fixture"), {})
            self.assertIn("CODE_SIGNING_ALLOWED=NO", command)
            self.assertIn("ONLY_ACTIVE_ARCH=NO", command)
            self.assertIn("Release", command)
            self.assertIn(beta.PLATFORMS[platform][1], command)
            self.assertEqual(command[-1], "build")
            self.assertFalse(any("Provisioning" in arg or arg in ("install", "launch", "archive")
                                 for arg in command))

    def test_cloud_submission_requires_explicit_full_source_commit(self):
        for argv in (
            ["upload", "ios", "--confirm-upload"],
            ["notarize", "mac", "--distribution", "developer-id", "--confirm-notarize"],
            ["archive", "ios", "--source-commit", "main"],
            ["archive", "ios", "--source-commit", "abc1234"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                beta.arguments([*argv, "--version", "0.1.0", "--build", "1"])

    def test_candidate_is_only_an_unapproved_local_signed_probe(self):
        for action, platform, extra in (
            ("build", "ios", []),
            ("upload", "ios", ["--confirm-upload"]),
            ("notarize", "mac", ["--distribution", "developer-id", "--confirm-notarize"]),
            ("archive", "ios", ["--source-commit", "a" * 40]),
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.args(action, platform, "--candidate", *extra)
        self.assertTrue(self.args("archive", "ios", "--candidate").candidate)

    def test_exact_clean_commit_gate(self):
        with patch.object(beta.subprocess, "check_output", side_effect=["a" * 40 + "\n", ""]):
            beta.verify_source("a" * 40)
        with patch.object(beta.subprocess, "check_output", return_value="b" * 40 + "\n"):
            with self.assertRaisesRegex(ValueError, "does not match"):
                beta.verify_source("a" * 40)
        for status in (" M project.yml\n", "M  App/file.swift\n", "?? new-source.swift\n"):
            with patch.object(beta.subprocess, "check_output", side_effect=["a" * 40 + "\n", status]):
                with self.assertRaisesRegex(ValueError, "dirty"):
                    beta.verify_source("a" * 40)

    def test_unsigned_inspection_can_remain_unfrozen_without_git_access(self):
        with patch.object(beta.subprocess, "check_output") as check:
            beta.verify_source(None)
            check.assert_not_called()

    def test_notarization_and_provisioning_require_separate_explicit_choices(self):
        invalid = [
            ("archive", "ios", "--distribution", "developer-id"),
            ("notarize", "mac", "--distribution", "developer-id"),
            ("notarize", "mac", "--confirm-notarize"),
            ("archive", "mac", "--confirm-notarize"),
            ("upload", "mac", "--distribution", "developer-id", "--confirm-upload"),
            ("build", "ios", "--allow-provisioning-updates"),
        ]
        for action, platform, *extra in invalid:
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.args(action, platform, *extra)

    def test_developer_id_archive_preserves_mcp_without_store_signing_inputs(self):
        args = self.args("archive", "mac", "--distribution", "developer-id")
        env = {"HOZZ_TEAM": "ABCDEFGHIJ", "HOZZ_MAC_PROFILE": "Hozz Developer ID"}
        errors = beta.prerequisites(args, env, "Developer ID Application: Example (ABCDEFGHIJ)")
        self.assertEqual(errors, [])
        command = beta.build_command(args, Path("build/fixture"), env)
        self.assertIn("CODE_SIGN_IDENTITY=Developer ID Application", command)
        self.assertIn("ENABLE_HARDENED_RUNTIME=YES", command)
        self.assertIn("OTHER_CODE_SIGN_FLAGS=--timestamp", command)
        self.assertNotIn("-allowProvisioningUpdates", command)
        options = beta.export_options(args, env)
        self.assertEqual(options["method"], "developer-id")
        self.assertNotIn("installerSigningCertificate", options)
        self.assertEqual(options["provisioningProfiles"], {"com.thatcube.Hozz.mac": "Hozz Developer ID"})

    def test_manual_developer_id_requires_profile_for_current_mac_entitlements(self):
        args = self.args("check", "mac", "--distribution", "developer-id")
        errors = beta.prerequisites(args, {"HOZZ_TEAM": "ABCDEFGHIJ"},
                                   "Developer ID Application: Example (ABCDEFGHIJ)")
        self.assertEqual(errors, ["Set HOZZ_MAC_PROFILE to an already-installed Developer ID profile name/UUID."])

    def test_automatic_provisioning_requires_named_account_credentials(self):
        args = self.args("archive", "ios", "--allow-provisioning-updates")
        errors = beta.prerequisites(args, {"HOZZ_TEAM": "ABCDEFGHIJ"}, "")
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("HOZZ_ASC_" in error for error in errors))
        with patch.object(Path, "is_file", return_value=True):
            self.assertEqual(beta.prerequisites(args, self.environment(), ""), [])
        command = beta.build_command(args, Path("build/fixture"), self.environment())
        self.assertIn("CODE_SIGN_STYLE=Automatic", command)
        self.assertIn("CODE_SIGN_IDENTITY=Apple Development", command)
        self.assertIn("-allowProvisioningUpdates", command)
        self.assertIn("-authenticationKeyPath", command)
        self.assertIn("HOZZ_IOS_PROFILE=", command)
        self.assertNotIn("-allowProvisioningDeviceRegistration", command)
        options = beta.export_options(args, self.environment())
        self.assertEqual(options["signingStyle"], "automatic")
        self.assertNotIn("provisioningProfiles", options)

    def test_developer_id_never_creates_an_identity_via_automatic_preflight(self):
        args = self.args("archive", "mac", "--distribution", "developer-id",
                         "--allow-provisioning-updates")
        with patch.object(Path, "is_file", return_value=True):
            errors = beta.prerequisites(args, self.environment(), "")
        self.assertEqual(errors, ["No usable Developer ID Application certificate/private key for team ABCDEFGHIJ."])

    def test_notarization_waits_and_never_creates_keychain_credentials(self):
        args = self.args("notarize", "mac", "--distribution", "developer-id", "--confirm-notarize")
        env = {"HOZZ_TEAM": "ABCDEFGHIJ", "HOZZ_MAC_PROFILE": "Hozz Developer ID",
               "HOZZ_NOTARY_KEYCHAIN_PROFILE": "Hozz existing credentials"}
        self.assertEqual(beta.prerequisites(args, env, "Developer ID Application: Example (ABCDEFGHIJ)"), [])
        command = beta.notarization_command(Path("build/fixture.zip"), env)
        self.assertIn("--wait", command)
        self.assertIn("1h", command)
        self.assertIn("--keychain-profile", command)
        self.assertNotIn("store-credentials", command)
        self.assertEqual(beta.notary_auth(self.environment())[0], "--key")

    def test_only_accepted_notary_result_can_proceed_to_stapling(self):
        report = {"id": "12345678-abcd-1234-abcd-123456789abc", "status": "Accepted"}
        beta.check_notary_result(report, 0)
        for status in ("In Progress", "Invalid", None):
            with self.assertRaisesRegex(ValueError, "not accepted"):
                beta.check_notary_result(dict(report, status=status), 0)
        with self.assertRaisesRegex(ValueError, "not accepted"):
            beta.check_notary_result(report, 1)
        with self.assertRaisesRegex(ValueError, "submission ID"):
            beta.check_notary_result({"status": "Accepted"}, 0)

    def test_signed_archive_uses_explicit_target_profiles_and_no_provisioning(self):
        for platform in beta.PLATFORMS:
            args = self.args("archive", platform)
            command = beta.build_command(args, Path("build/fixture"), self.environment())
            self.assertIn("CODE_SIGN_STYLE=Manual", command)
            self.assertIn("CODE_SIGN_IDENTITY=Apple Distribution", command)
            self.assertIn("MARKETING_VERSION=0.1.0", command)
            self.assertIn("CURRENT_PROJECT_VERSION=42", command)
            self.assertIn("HOZZ_CLINICAL_FLAG=", command)
            self.assertEqual(command[-1], "archive")
            self.assertNotIn("-allowProvisioningUpdates", command)
            for name in beta.PROFILE_VARIABLES[platform].values():
                self.assertIn(f"{name}={self.environment()[name]}", command)

    def test_export_preserves_explicit_version_and_symbols(self):
        for platform in beta.PLATFORMS:
            options = beta.export_options(self.args("archive", platform), self.environment())
            self.assertEqual(options["method"], "app-store-connect")
            self.assertEqual(options["destination"], "export")
            self.assertFalse(options["manageAppVersionAndBuildNumber"])
            self.assertTrue(options["uploadSymbols"])
            self.assertEqual(set(options["provisioningProfiles"]), set(beta.PROFILE_VARIABLES[platform]))

    def test_missing_signing_inputs_and_wrong_team_fail(self):
        errors = beta.prerequisites(self.args("archive"), {}, "")
        self.assertEqual(len(errors), 3)
        errors = beta.prerequisites(self.args("archive"), self.environment(),
                                   '1) Apple Distribution: Someone (ZYXWVUTSRQ)')
        self.assertTrue(any("No usable Apple Distribution" in error for error in errors))

    def test_mac_requires_separate_installer_identity(self):
        errors = beta.prerequisites(self.args("archive", "mac"), self.environment(),
                                   '1) Apple Distribution: Example (ABCDEFGHIJ)')
        self.assertEqual(len(errors), 1)
        self.assertIn("Installer", errors[0])

    def test_upload_requires_existing_credentials(self):
        args = self.args("upload", "ios", "--confirm-upload")
        errors = beta.prerequisites(args, self.environment(),
                                   '1) Apple Distribution: Example (ABCDEFGHIJ)')
        self.assertEqual(errors, ["HOZZ_ASC_API_KEY_JSON does not point to an existing file."])

    def test_upload_waits_without_distributing_or_changing_signing(self):
        for platform, package_flag in (("ios", "--ipa"), ("mac", "--pkg")):
            args = self.args("upload", platform, "--confirm-upload")
            command = beta.upload_command(args, Path("build/package"), self.environment())
            self.assertIn(package_flag, command)
            self.assertEqual(command[command.index("--skip_submission") + 1], "true")
            self.assertEqual(command[command.index("--distribute_external") + 1], "false")
            self.assertEqual(command[command.index("--skip_waiting_for_build_processing") + 1], "false")
            self.assertEqual(command[command.index("--wait_processing_timeout_duration") + 1], "3600")

    def test_mismatched_bundle_or_version_is_rejected(self):
        info = {"CFBundleIdentifier": "com.thatcube.Hozz", "CFBundleVersion": "42",
                "CFBundleShortVersionString": "0.1.0"}
        beta.check_metadata(info, "com.thatcube.Hozz", self.args())
        for key in info:
            with self.assertRaises(ValueError):
                beta.check_metadata(dict(info, **{key: "wrong"}), "com.thatcube.Hozz", self.args())

    def test_missing_capabilities_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "entitlement"):
            beta.validate_entitlements({}, "ios")
        with self.assertRaisesRegex(ValueError, "entitlement"):
            beta.validate_entitlements({}, "mac")
        beta.validate_entitlements({"com.apple.security.application-groups":
                                   ["group.com.thatcube.Hozz"]}, "ios", widget=True)

    def test_worker_cannot_bypass_lease(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "shared Apple build lease"):
                beta.lease_descriptors()

    def test_source_keychain_placeholder_is_not_a_signed_entitlement(self):
        with (ROOT / "Mac/HozzMac.entitlements").open("rb") as source:
            entitlements = plistlib.load(source)
        beta.validate_entitlements(entitlements, "mac", source=True)
        with self.assertRaisesRegex(ValueError, "Keychain"):
            beta.validate_entitlements(entitlements, "mac")

    def test_lease_passes_only_authenticated_descriptors_to_validator(self):
        env = {"APPLE_BUILD_LEASE_PROTOCOL": "1", "APPLE_BUILD_LEASE_MODE": "shared",
               "APPLE_BUILD_LEASE_OWNER": "fixture", "APPLE_BUILD_LEASE_ID": "fixture",
               "APPLE_BUILD_LEASE_TOKEN": "fixture", "APPLE_BUILD_LEASE_LOCK_FD": "9",
               "APPLE_BUILD_LEASE_PROOF_FD": "8"}
        with patch.dict(os.environ, env, clear=True), patch.object(beta.subprocess, "run") as run:
            run.return_value.returncode = 10
            self.assertEqual(beta.lease_descriptors(), (9, 8))
            self.assertEqual(run.call_args.kwargs["pass_fds"], (9, 8))
            run.return_value.returncode = 75
            with self.assertRaisesRegex(ValueError, "validation failed"):
                beta.lease_descriptors()

    def test_new_shell_lane_validates_inputs_before_acquiring_lease(self):
        shell = (ROOT / "tools/apple-beta.sh").read_text()
        self.assertLess(shell.index("python3 tools/lib/apple_beta.py validate"),
                        shell.index("acquire_apple_build_shared_lease"))
        self.assertLess(shell.index("install_apple_build_lease_traps"),
                        shell.index("python3 tools/lib/apple_beta.py run"))

    def test_mac_tool_uses_native_archive_aware_copy_phase(self):
        project = (ROOT / "project.yml").read_text()
        mac = project.split("  HozzMac:\n", 1)[1].split("\nschemes:", 1)[0]
        self.assertIn(
            "- target: hozz-mcp\n        embed: true\n        link: false\n"
            "        codeSign: true\n        copy:\n          destination: executables",
            mac,
        )
        self.assertNotIn("postBuildScripts:", mac)
        self.assertIn("ENABLE_USER_SCRIPT_SANDBOXING: true", project)

    def test_distribution_signature_must_match_team_and_reject_debugger(self):
        details = "Authority=Apple Distribution: Example (ABCDEFGHIJ)\nTeamIdentifier=ABCDEFGHIJ\n"
        with patch.object(beta.subprocess, "run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stderr=details),
                subprocess.CompletedProcess([], 0, stdout=plistlib.dumps({"get-task-allow": True})),
            ]
            with self.assertRaisesRegex(ValueError, "get-task-allow"):
                beta.signed_entitlements(Path("fixture.app"), "ABCDEFGHIJ")
        with patch.object(beta.subprocess, "run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stderr=details),
            ]
            with self.assertRaisesRegex(ValueError, "requested Apple Distribution team"):
                beta.signed_entitlements(Path("fixture.app"), "WRONGTEAM1")

    def test_developer_id_signature_requires_runtime_and_secure_timestamp(self):
        details = "Authority=Developer ID Application: Example (ABCDEFGHIJ)\nTeamIdentifier=ABCDEFGHIJ\n"
        for suffix, error in (("", "Hardened Runtime"), ("CodeDirectory flags=0x10000(runtime)\n", "timestamp")):
            with patch.object(beta.subprocess, "run") as run:
                run.side_effect = [
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 0, stderr=details + suffix),
                ]
                with self.assertRaisesRegex(ValueError, error):
                    beta.signed_entitlements(Path("fixture.app"), "ABCDEFGHIJ",
                                             identity="Developer ID Application", hardened=True)

    def test_automatic_development_archive_is_not_a_valid_export(self):
        details = "Authority=Apple Development: Example (ABCDEFGHIJ)\nTeamIdentifier=ABCDEFGHIJ\n"
        with patch.object(beta.subprocess, "run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stderr=details),
                subprocess.CompletedProcess([], 0, stdout=plistlib.dumps({"get-task-allow": True})),
            ]
            beta.signed_entitlements(Path("fixture.app"), "ABCDEFGHIJ", development_archive=True)
        with patch.object(beta.subprocess, "run") as run:
            run.side_effect = [
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 0, stderr=details),
            ]
            with self.assertRaisesRegex(ValueError, "requested Apple Distribution"):
                beta.signed_entitlements(Path("fixture.app"), "ABCDEFGHIJ")

    def test_privacy_manifests_match_actual_api_categories(self):
        expected = {
            "Sources/HozzDeliver": {"UserDefaults": {"CA92.1"}},
            "Sources/HozzStore": {"FileTimestamp": {"C617.1"}},
            "Sources/HozzHealth": {"FileTimestamp": {"C617.1"}},
            "Sources/HozzReceive": {"FileTimestamp": {"C617.1", "3B52.1"},
                                    "DiskSpace": {"85F4.1", "E174.1"}},
            "Mac": {"UserDefaults": {"CA92.1"}},
        }
        for folder, categories in expected.items():
            with (ROOT / folder / "PrivacyInfo.xcprivacy").open("rb") as source:
                manifest = plistlib.load(source)
            actual = {entry["NSPrivacyAccessedAPIType"].removeprefix("NSPrivacyAccessedAPICategory"):
                      set(entry["NSPrivacyAccessedAPITypeReasons"])
                      for entry in manifest["NSPrivacyAccessedAPITypes"]}
            self.assertEqual(actual, categories)
            self.assertFalse(manifest["NSPrivacyTracking"])
            self.assertEqual(manifest["NSPrivacyCollectedDataTypes"], [])


class AppleSourceFreezePipelineTests(unittest.TestCase):
    """Exercise the real run/notarize control flow with synthetic tool outputs."""

    def exercise(self, action, change=None, dirty_start=False, candidate=False):
        fixture = ROOT / "build/apple-source-freeze-fixtures" / uuid.uuid4().hex
        fixture.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, fixture)
        (fixture / "Local.xcconfig").write_text("")
        platform = "mac" if action == "notarize" else "ios"
        argv = [action, platform, "--version", "0.1.0", "--build", "1"]
        if action in ("upload", "notarize"):
            argv += ["--source-commit", "a" * 40, "--confirm-" + action]
        if action == "notarize":
            argv += ["--distribution", "developer-id"]
        if candidate:
            argv += ["--candidate"]
        args = beta.arguments(argv)
        state = {"head": "a" * 40, "dirty": " M source.swift\n" if dirty_start else "",
                 "submits": 0, "export_validations": 0, "commands": []}
        real_logged = beta.run_logged
        real_open = Path.open
        env = {"HOZZ_TEAM": "ABCDEFGHIJ", "HOZZ_IOS_PROFILE": "app-profile",
               "HOZZ_WIDGET_PROFILE": "widget-profile", "HOZZ_MAC_PROFILE": "mac-profile",
               "HOZZ_ASC_API_KEY_JSON": "/fixture/auth.json", "HOZZ_NOTARY_KEYCHAIN_PROFILE": "fixture"}

        def mutate(stage):
            if change and change[0] == stage:
                if change[1] == "head":
                    state["head"] = "b" * 40
                else:
                    state["dirty"] = change[1]

        def output(command, **kwargs):
            if command[:2] == ["git", "rev-parse"]:
                value = state["head"] + "\n"
            elif command[:2] == ["git", "status"]:
                value = state["dirty"]
            elif command[0] == "security":
                value = "fixture identity"
            else:
                self.fail(f"Unexpected unmocked command: {command[0]}")
            return value if kwargs.get("text") else value.encode()

        def logged(command, logfile, descriptors, env=None, source_commit=None):
            state["commands"].append(command)
            if command[0] == "fastlane":
                mutate("late_submit")
                return real_logged(command, logfile, descriptors, env, source_commit)
            logfile.write_text("Synthetic tool output.\n")
            if command[0] == "xcodebuild" and command[-1] == "archive":
                mutate("archive")
            elif command[:2] == ["xcodebuild", "-exportArchive"]:
                exported = Path(command[command.index("-exportPath") + 1])
                exported.mkdir()
                if platform == "ios":
                    (exported / "Hozz.ipa").write_bytes(b"fixture-ipa")
                else:
                    (exported / "Hozz.app").mkdir()
                mutate("export")
            elif command[:3] == ["ditto", "-c", "-k"]:
                Path(command[-1]).write_bytes(b"fixture-zip")

        def open_file(path, *args, **kwargs):
            if path.name == "notary-submission.stderr.log":
                mutate("late_submit")
            return real_open(path, *args, **kwargs)

        def validate(app, args, directory, env, exported=False):
            if exported:
                state["export_validations"] += 1
                final_validation = 2 if platform == "mac" else 1
                if state["export_validations"] == final_validation:
                    mutate("submit")
            return {"app": str(app), "dsyms": str(directory / "dSYMs"), "signed": True,
                    "uuids": [], "validation": "signed-archive"}

        def submit(command, **kwargs):
            state["submits"] += 1
            if command[0] == "fastlane":
                return subprocess.CompletedProcess(command, 0)
            self.assertEqual(command[:3], ["xcrun", "notarytool", "submit"])
            kwargs["stdout"].write(json.dumps({
                "id": "12345678-abcd-1234-abcd-123456789abc", "status": "Accepted"
            }).encode())
            return subprocess.CompletedProcess(command, 0)

        error = None
        with patch.object(beta, "ROOT", fixture), patch.dict(os.environ, env, clear=True), \
                patch.object(beta, "lease_descriptors", return_value=()), \
                patch.object(beta, "prerequisites", return_value=[]), \
                patch.object(beta.shutil, "which", return_value="/fixture/fastlane"), \
                patch.object(beta.subprocess, "check_output", side_effect=output), \
                patch.object(beta.subprocess, "run", side_effect=submit), \
                patch.object(beta, "run_logged", side_effect=logged), \
                patch.object(beta, "validate_artifact", side_effect=validate), \
                patch.object(Path, "open", open_file), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                beta.run(args)
            except ValueError as failure:
                error = str(failure)
        receipts = list(fixture.glob("build/apple-beta/*/evidence.json"))
        receipt = json.loads(receipts[0].read_text()) if receipts else None
        return state, error, receipt

    def test_dirty_start_never_reaches_upload_or_notarization(self):
        for action in ("upload", "notarize"):
            with self.subTest(action=action):
                state, error, receipt = self.exercise(action, dirty_start=True)
                self.assertIn("dirty", error)
                self.assertEqual(state["commands"], [])
                self.assertEqual(state["submits"], 0)
                self.assertIsNone(receipt)

    def test_changed_HEAD_after_archive_export_or_before_submit_is_rejected(self):
        for action in ("upload", "notarize"):
            for stage in ("archive", "export", "submit", "late_submit"):
                with self.subTest(action=action, stage=stage):
                    state, error, receipt = self.exercise(action, change=(stage, "head"))
                    self.assertIn("does not match", error)
                    self.assertEqual(state["submits"], 0)
                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(receipt["expected_source_commit"], "a" * 40)
                    self.assertFalse(receipt["upload_attempted"])
                    self.assertFalse(receipt["notarization_attempted"])
                    self.assertFalse(receipt["eligible_for_apple_submission"])

    def test_dirtied_tracked_or_untracked_source_never_reaches_either_submit(self):
        for action in ("upload", "notarize"):
            for stage in ("archive", "export", "submit", "late_submit"):
                for status in (" M source.swift\n", "?? newly-created.swift\n"):
                    with self.subTest(action=action, stage=stage, status=status):
                        state, error, receipt = self.exercise(action, change=(stage, status))
                        self.assertIn("dirty", error)
                        self.assertEqual(state["submits"], 0)
                        self.assertEqual(receipt["status"], "failed")
                        self.assertFalse(receipt["eligible_for_apple_submission"])

    def test_clean_frozen_upload_and_notarization_positive_controls(self):
        for action in ("upload", "notarize"):
            with self.subTest(action=action):
                state, error, receipt = self.exercise(action)
                self.assertIsNone(error)
                self.assertEqual(state["submits"], 1)
                self.assertEqual(receipt["status"], "succeeded")
                self.assertEqual(receipt["git_commit"], "a" * 40)
                self.assertFalse(receipt["git_dirty"])
                self.assertTrue(receipt["eligible_for_apple_submission"])

    def test_signed_archive_requires_clean_tree_and_freezes_HEAD_by_default(self):
        state, error, receipt = self.exercise("archive", dirty_start=True)
        self.assertIn("dirty", error)
        self.assertEqual(state["commands"], [])
        state, error, receipt = self.exercise("archive")
        self.assertIsNone(error)
        self.assertEqual(receipt["expected_source_commit"], "a" * 40)
        self.assertTrue(receipt["eligible_for_apple_submission"])
        state, error, receipt = self.exercise("archive", change=("export", "head"))
        self.assertIn("does not match", error)
        self.assertEqual(receipt["status"], "failed")

    def test_explicit_dirty_candidate_is_never_labelled_distributable(self):
        state, error, receipt = self.exercise("archive", dirty_start=True, candidate=True)
        self.assertIsNone(error)
        self.assertEqual(state["submits"], 0)
        self.assertTrue(receipt["local_candidate"])
        self.assertTrue(receipt["git_dirty"])
        self.assertEqual(receipt["validation"], "signed-candidate-non-distributable")
        self.assertFalse(receipt["eligible_for_apple_submission"])


if __name__ == "__main__":
    unittest.main()
