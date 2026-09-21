#!/usr/bin/env python3
"""Read-only account tooling regression tests: no credentials or network required."""

import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import apple_account as account


class AppleAccountTests(unittest.TestCase):
    def inventory(self):
        return {
            "query_failures": [],
            "apps": {"com.thatcube.Hozz": [{"id": "APP"}], "com.thatcube.Hozz.mac": []},
            "bundle_ids": {
                bundle: [{"id": f"BUNDLE{index}", "seedId": "ABCDEFGHIJ", "profiles": []}]
                for index, bundle in enumerate(account.BUNDLES)
            },
            "certificates": [{"id": "DEVID", "certificateType": "DEVELOPER_ID_APPLICATION_G2",
                              "local_private_key_identity_present": True, "expirationDate": "2999-01-01T00:00:00Z"}],
        }

    def test_der_signature_is_padded_to_jwt_es256_format(self):
        der = bytes.fromhex("3006020101020102")
        self.assertEqual(account.raw_ecdsa_signature(der), b"\0" * 31 + b"\1" + b"\0" * 31 + b"\2")
        for invalid in (b"", b"not a signature", der + b"x", bytes.fromhex("3006020101020202")):
            with self.assertRaises(ValueError):
                account.raw_ecdsa_signature(invalid)

    def test_jwt_uses_authorized_key_path_short_expiry_and_no_logged_key_material(self):
        env = {"HOZZ_ASC_KEY_PATH": "/private/existing-key.p8", "HOZZ_ASC_KEY_ID": "ABCDEFGHIJ",
               "HOZZ_ASC_ISSUER_ID": "12345678-abcd-1234-abcd-123456789abc"}
        with patch.object(account.time, "time", return_value=1000), patch.object(account.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=bytes.fromhex("3006020101020102"))
            bearer = account.token(env)
            header, payload, signature = bearer.split(".")
            decode = lambda value: json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            self.assertEqual(decode(header)["kid"], env["HOZZ_ASC_KEY_ID"])
            self.assertEqual(decode(payload)["exp"], 1600)
            self.assertEqual(decode(payload)["aud"], "appstoreconnect-v1")
            self.assertIn(env["HOZZ_ASC_KEY_PATH"], run.call_args.args[0])
            self.assertNotIn("existing-key", bearer)
            self.assertEqual(len(base64.urlsafe_b64decode(signature + "==")), 64)

    def test_signing_failure_never_echoes_openssl_stderr(self):
        env = {"HOZZ_ASC_KEY_PATH": "/private/key.p8", "HOZZ_ASC_KEY_ID": "ABCDEFGHIJ",
               "HOZZ_ASC_ISSUER_ID": "12345678-abcd-1234-abcd-123456789abc"}
        with patch.object(account.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, stderr=b"secret-key-material")
            with self.assertRaises(ValueError) as error:
                account.token(env)
            self.assertNotIn("secret-key-material", str(error.exception))

    def test_api_transport_only_gets_from_fixed_origin(self):
        client = account.ReadOnlyClient("fake-bearer")
        response = Mock()
        response.status = 200
        response.read.return_value = b'{"data":[]}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        client.opener = Mock()
        client.opener.open.return_value = response
        self.assertEqual(client.get("/v1/apps", {"filter[bundleId]": "com.thatcube.Hozz"}), {"data": []})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.startswith(account.API + "/v1/apps?"))
        self.assertNotIn("fake-bearer", json.dumps(client.requests))
        for route in ("https://other.invalid/v1/apps", "//other.invalid/v1/apps", "/v2/apps"):
            with self.assertRaises(ValueError):
                client.get(route)

    def test_redirects_and_cross_origin_pagination_cannot_leak_token(self):
        self.assertIsNone(account.NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.invalid"))
        client = account.ReadOnlyClient("fake-bearer")
        with patch.object(client, "get", return_value={"data": [], "links": {"next": "https://other.invalid/v1/apps"}}):
            with self.assertRaisesRegex(ValueError, "pagination origin"):
                client.all("/v1/apps")

    def test_http_error_records_only_status_and_safe_error_codes(self):
        body = b'{"errors":[{"code":"FORBIDDEN_ERROR","detail":"secret-response-content"}]}'
        client = account.ReadOnlyClient("fake-bearer")
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError(account.API + "/v1/apps", 403, "Forbidden", {}, io.BytesIO(body))
        with self.assertRaises(ValueError) as error:
            client.get("/v1/apps")
        self.assertNotIn("secret-response-content", str(error.exception))
        self.assertNotIn("secret-response-content", json.dumps(client.requests))
        self.assertEqual(client.requests[0]["error_codes"], ["FORBIDDEN_ERROR"])

    def test_certificate_and_profile_bodies_are_never_persisted(self):
        encoded = base64.b64encode(b"public-certificate-fixture").decode()
        fingerprint = hashlib.sha1(b"public-certificate-fixture").hexdigest().upper()
        certificate = account.certificate(
            {"id": "CERT", "type": "certificates", "attributes":
                {"certificateType": "DEVELOPMENT", "certificateContent": encoded}}, {fingerprint})
        self.assertTrue(certificate["local_private_key_identity_present"])
        self.assertNotIn(encoded, json.dumps(certificate))
        profile = account.selected(
            {"id": "PROFILE", "attributes": {"name": "Hozz", "profileContent": "do-not-persist"}},
            ("name", "profileType", "profileState"))
        self.assertNotIn("do-not-persist", json.dumps(profile))

    def test_plan_reuses_exact_app_and_developer_id_identity_without_writes(self):
        result = account.proposed_changes(self.inventory(), "ABCDEFGHIJ")
        self.assertTrue(result["ready_for_review"])
        self.assertFalse(result["cloud_mutations_performed"])
        self.assertEqual(result["ios_app_record"], "Already exists; no creation.")
        mac = next(item for item in result["profiles"] if item["profile_type"] == "MAC_APP_DIRECT")
        self.assertTrue(mac["requires_confirmation"])
        self.assertEqual(mac["path"], "/v1/profiles")
        self.assertEqual(mac["proposed_body"]["data"]["relationships"]["certificates"]["data"],
                         [{"type": "certificates", "id": "DEVID"}])
        self.assertTrue(all("blocked_by" in item for item in result["profiles"] if item["profile_type"] == "IOS_APP_STORE"))
        self.assertIn("Not required", result["mac_app_record"])

    def test_incomplete_inventory_or_wrong_team_cannot_propose_mutations(self):
        inventory = self.inventory()
        inventory["query_failures"] = [{"resource": "apps", "error": "HTTP 403"}]
        self.assertFalse(account.proposed_changes(inventory, "ABCDEFGHIJ")["ready_for_review"])
        self.assertFalse(account.proposed_changes(self.inventory(), "OTHERTEAM1")["ready_for_review"])

    def test_expired_assets_are_not_recommended_for_reuse(self):
        inventory = self.inventory()
        inventory["certificates"][0]["expirationDate"] = "2000-01-01T00:00:00Z"
        inventory["bundle_ids"]["com.thatcube.Hozz.mac"][0]["profiles"] = [
            {"id": "OLD", "profileType": "MAC_APP_DIRECT", "profileState": "ACTIVE",
             "expirationDate": "2000-01-01T00:00:00Z"}]
        result = account.proposed_changes(inventory, "ABCDEFGHIJ")
        mac = next(item for item in result["profiles"] if item["profile_type"] == "MAC_APP_DIRECT")
        self.assertIn("blocked_by", mac)


if __name__ == "__main__":
    unittest.main()
