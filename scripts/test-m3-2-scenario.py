"""Pure scenario-client tests; no HTTP, SQL, chain or process interaction."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m3_2_scenario as s


class Gateway:
    roles = {"aiSigner": "0x" + "11" * 20, "humanApprover": "0x" + "22" * 20, "recipient": "0x" + "33" * 20}

    def contract_address(self, name):
        return "0x" + ("44" if name == "PoGRegistryV2" else "55") * 20


def request(kind):
    primary, contract, fields, _, role = s.SIGNING[kind]
    m = {n: 0 if t.startswith("uint") else "0x" + "00" * (20 if t == "address" else 32) for n, t in fields}
    m["expectedRecipient" if kind == "receipt" else "signer"] = Gateway.roles[role]
    m["nonce"], m["deadline"] = 7, 1000
    return {"signer": Gateway.roles[role], "nonce": "7", "deadline": "1000", "synthetic": kind == "ai_pre",
            "typedData": {"primaryType": primary, "domain": {"name": contract, "version": "2", "chainId": 31337,
                          "verifyingContract": Gateway().contract_address(contract)},
                          "types": {"EIP712Domain": [{"name": n, "type": t} for n, t in s.DOMAIN_FIELDS],
                                    primary: [{"name": n, "type": t} for n, t in fields]}, "message": m}}


class Response:
    def __init__(self, status, value):
        self.status_code, self.value = status, value

    def json(self):
        return self.value

    @property
    def text(self):
        raise AssertionError("Secret response body must not be read")


class ScenarioUnit(unittest.TestCase):
    def test_literal_loopback(self):
        self.assertEqual(s._loopback_url("http://127.0.0.1:19001"), "http://127.0.0.1:19001")

    def test_reject_url_credentials_paths_redirect_targets(self):
        for url in ("http://localhost:19001", "https://127.0.0.1:19001", "http://127.0.0.1:19001/",
                    "http://user:secret@127.0.0.1:19001", "http://127.0.0.1:19001?x=y", "http://127.0.0.1:80", "http://127.0.0.1:99999"):
            with self.subTest(url=url), self.assertRaises(s.ScenarioError):
                s._loopback_url(url)

    def test_exact_three_typed_shapes(self):
        for kind in s.SIGNING:
            value = request(kind)
            self.assertIs(s._typed_shape(kind, value, Gateway()), value["typedData"])

    def test_field_order(self):
        value = request("ai_pre")
        value["typedData"]["types"]["AIAssessment"].reverse()
        with self.assertRaises(s.ScenarioError):
            s._typed_shape("ai_pre", value, Gateway())

    def test_field_width(self):
        value = request("reserve")
        value["typedData"]["types"]["HumanIntent"][-1]["type"] = "uint256"
        with self.assertRaises(s.ScenarioError):
            s._typed_shape("reserve", value, Gateway())

    def test_wrong_domain(self):
        for field, wrong in (("name", "PoGRegistry"), ("version", "1"), ("chainId", 1), ("verifyingContract", "0x" + "77" * 20)):
            value = request("receipt")
            value["typedData"]["domain"][field] = wrong
            with self.subTest(field=field), self.assertRaises(s.ScenarioError):
                s._typed_shape("receipt", value, Gateway())

    def test_extra_message_field(self):
        value = request("reserve")
        value["typedData"]["message"]["instanceId"] = "forbidden"
        with self.assertRaises(s.ScenarioError):
            s._typed_shape("reserve", value, Gateway())

    def test_wrong_recipient_signer(self):
        value = request("receipt")
        value["typedData"]["message"]["expectedRecipient"] = Gateway.roles["aiSigner"]
        with self.assertRaises(s.ScenarioError):
            s._typed_shape("receipt", value, Gateway())

    def test_nonce_deadline(self):
        for field in ("nonce", "deadline"):
            value = request("ai_pre")
            value[field] = "2001"
            with self.subTest(field=field), self.assertRaises(s.ScenarioError):
                s._typed_shape("ai_pre", value, Gateway())

    def test_synthetic_label(self):
        value = request("ai_pre")
        value["synthetic"] = False
        with self.assertRaises(s.ScenarioError):
            s._typed_shape("ai_pre", value, Gateway())

    def test_response_error_no_body_echo(self):
        with self.assertRaises(s.ScenarioError) as error:
            s._response(Response(422, {"signature": "SECRET"}), 202, "submit")
        self.assertNotIn("SECRET", str(error.exception))

    def test_json_response_object(self):
        with self.assertRaises(s.ScenarioError):
            s._response(Response(202, []), 202, "submit")

    def test_owned_guard_before_connection(self):
        calls = []
        with patch.dict(os.environ, {"POG_M3_2_SCENARIO_DATABASE_URL": "owned", "POG_TEST_DATABASE_URL": "owned",
                                    "POG_MANAGED_POSTGRES_STATE": "/owned/pg"}, clear=True):
            s._database_guard("owned", lambda value: calls.append(value))
        self.assertEqual(calls, ["owned"])

    def test_missing_grant_fails_before_dependency(self):
        calls = []
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(s.ScenarioError):
            s._database_guard("group-db", lambda *a: calls.append(True))
        self.assertEqual(calls, [])

    def test_mismatched_grant_fails_before_dependency(self):
        calls = []
        with patch.dict(os.environ, {"POG_M3_2_SCENARIO_DATABASE_URL": "owned", "POG_TEST_DATABASE_URL": "group-db",
                                    "POG_MANAGED_POSTGRES_STATE": "/owned/pg"}, clear=True), self.assertRaises(s.ScenarioError):
            s._database_guard("group-db", lambda *a: calls.append(True))
        self.assertEqual(calls, [])

    def test_private_context_never_json_or_repr_secret(self):
        context = s.ResetContext("p", "q", "r", "o", "run", "instance", "SECRET_SIGNATURE")
        self.assertNotIn("SECRET_SIGNATURE", repr(context))
        with self.assertRaises(TypeError):
            json.dumps(context)

    def test_only_authorized_signing_kinds(self):
        self.assertEqual(set(s.SIGNING), {"ai_pre", "reserve", "receipt"})

    def test_202_is_not_chain_confirmation(self):
        client = s._Scenario(None, {}, None, None, None)
        with self.assertRaises(s.ScenarioError):
            client.queued({"operation": {"status": "confirmed", "chainVerified": True}}, "recipient")

    def test_exact_operation_and_resource_binding(self):
        s._resource_binding({"operationId": "o", "resourceType": "signing_request", "resourceId": "r"},
                            "o", ("signing_request", "r"))

    def test_resource_binding_requires_independent_expected_target(self):
        with self.assertRaises(s.ScenarioError):
            s._resource_binding({"operationId": "o"}, "o", None)

    def test_operation_binding_rejects_another_operation(self):
        with self.assertRaises(s.ScenarioError):
            s._resource_binding({"operationId": "other", "resourceType": "project", "resourceId": "p"}, "o", ("project", "p"))

    def test_resource_binding_rejects_another_type(self):
        with self.assertRaises(s.ScenarioError):
            s._resource_binding({"operationId": "o", "resourceType": "procurement", "resourceId": "p"}, "o", ("project", "p"))

    def test_resource_binding_rejects_another_uuid(self):
        with self.assertRaises(s.ScenarioError):
            s._resource_binding({"operationId": "o", "resourceType": "project", "resourceId": "other"}, "o", ("project", "p"))

    def test_signing_prepare_rejects_another_procurement(self):
        client = s._Scenario(None, {}, None, None, None)
        client.post = lambda *args: {"signingRequest": {"procurementId": "other", "kind": "ai_pre"}}
        with self.assertRaises(s.ScenarioError):
            client.sign_and_submit({"id": "p"}, "ai_pre")

    def test_signing_prepare_rejects_another_kind(self):
        client = s._Scenario(None, {}, None, None, None)
        client.post = lambda *args: {"signingRequest": {"procurementId": "p", "kind": "receipt"}}
        with self.assertRaises(s.ScenarioError):
            client.sign_and_submit({"id": "p"}, "ai_pre")


class OwnedInvocationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pog-local-m3-2-unit-", dir="/tmp")
        self.root = Path(self.directory.name).resolve()
        (self.root / "chain").mkdir()
        self.manifest = self.root / "chain/manifest.json"
        self.manifest.write_text(json.dumps({"chain": {"rpcUrl": "http://127.0.0.1:40202"}}))
        self.owner = {"owner": "pog-blockchain-m3-2", "ports": [40201, 40202, 40203],
                      "invocation": "1" * 32, "sourceCandidate": "a" * 40}
        (self.root / "owner.json").write_text(json.dumps(self.owner))
        (self.root / "pg").mkdir()
        (self.root / "pg/managed.json").write_text(json.dumps({"host": "127.0.0.1", "port": 40201}))
        self.environment = {"POG_MANAGED_POSTGRES_STATE": str(self.root / "pg"),
                            "POG_M3_2_INVOCATION": "1" * 32, "POG_M3_2_API_SHA": "a" * 40,
                            "POG_TEST_DATABASE_URL": "postgresql+psycopg://pog_api@127.0.0.1:40201/pog_api_test"}

    def tearDown(self):
        self.directory.cleanup()

    def test_same_owned_invocation_is_required(self):
        with patch.dict(os.environ, self.environment, clear=True):
            s._owned_context("http://127.0.0.1:40203", self.manifest, self.root)

    def test_default_or_other_group_rpc_port_rejected(self):
        self.owner["ports"][1] = 8545
        (self.root / "owner.json").write_text(json.dumps(self.owner))
        with patch.dict(os.environ, self.environment, clear=True), \
                self.assertRaises(s.ScenarioError):
            s._owned_context("http://127.0.0.1:40203", self.manifest, self.root)

    def test_different_harness_database_state_rejected(self):
        with patch.dict(os.environ, {**self.environment, "POG_MANAGED_POSTGRES_STATE": "/other/team/pg"}, clear=True), \
                self.assertRaises(s.ScenarioError):
            s._owned_context("http://127.0.0.1:40203", self.manifest, self.root)

    def test_different_http_port_rejected(self):
        with patch.dict(os.environ, self.environment, clear=True), \
                self.assertRaises(s.ScenarioError):
            s._owned_context("http://127.0.0.1:40204", self.manifest, self.root)

    def test_same_directory_cannot_redirect_database_to_team_port(self):
        (self.root / "pg/managed.json").write_text(json.dumps({"host": "127.0.0.1", "port": 55433}))
        env = {**self.environment, "POG_TEST_DATABASE_URL": "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test"}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(s.ScenarioError):
            s._owned_context("http://127.0.0.1:40203", self.manifest, self.root)

    def test_wrong_candidate_grant_rejected(self):
        with patch.dict(os.environ, {**self.environment, "POG_M3_2_API_SHA": "b" * 40}, clear=True), \
                self.assertRaises(s.ScenarioError):
            s._owned_context("http://127.0.0.1:40203", self.manifest, self.root)

    def test_missing_invocation_grant_rejected(self):
        env = {k: v for k, v in self.environment.items() if k != "POG_M3_2_INVOCATION"}
        with patch.dict(os.environ, env, clear=True), self.assertRaises(s.ScenarioError):
            s._owned_context("http://127.0.0.1:40203", self.manifest, self.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
