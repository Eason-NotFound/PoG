#!/usr/bin/env python3
"""Offline oracle tests: static nonzero vectors and explicitly fake getters.

Golden vectors were generated independently with 32-byte manual ABI word
padding + Foundry 1.8.4 `cast keccak`; not by this module or pog_api. All values
are synthetic, and the fully nonzero late-stage vector is a FORMULA vector,
not an executed procurement lifecycle. No RPC, DB, signer or API is contacted.
Run using the candidate's locked Python runtime with eth_abi/eth_utils installed.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m3_2_hash_oracle as oracle


def b(number: int) -> str:
    return "0x" + f"{number:02x}" * 32


def a(number: int) -> str:
    return "0x" + f"{number:02x}" * 20


GOLDEN = {
    "preEvidenceHash": "0x3da5fc261a4b28402d36cf935f1cb0e260178d118c6d24e3d64d5b9f610b342d",
    "finalEvidenceHash": "0x0430239faa52a5acfa483e7af7a1ba93b6d718175e4da77c1e2cc687236558f1",
    "partiesHash": "0x3b28480ae64c8a1c93539ea704a5274349838df86e8d218fa6a8d4bb42114715",
    "cancellationEvidenceHash": "0x3d4687caa0914dd46dd15735adfc6a6c1f9323c843fb66a45eff10ba7f0b08aa",
    "ledgerHash": "0x1e14a983bcc922faa6b93ec80b4c9e69e227a203785409516f4931197af52cc2",
    "reserveTermsHash": "0x060aa88b306981dcff179483ca09b87eb6f2237035c47e1d9f553b3f9c71c6c4",
    "releaseTermsHash": "0x2d568a72702c2e61449d6ee5fe54173f1e40357a259b2b17a18b3434c777925f",
    "settlementTermsHash": "0x3ce15dc5d21d570433a1aba1bef818008b3e3df2cd5504a307731e5cc6b340d0",
    "cancellationTermsHash": "0x5d0ee128b4c0469bac96c73c87b9c52ce3b2254ef4660513d6d17a862c5ffe8d",
    "closeTermsHash": "0xd4d9044805edd202eda81f359db564ef524d3a5423326c2a29ddb77cc5c64d1c",
}
DIGESTS = {
    "AIAssessment": "0x1ce6c7297ddbaa6aa058a6f800618051d1b63db7f56e1b5a7fbb44286e756b21",
    "RecipientReceipt": "0x1d4f6bddd6ddafbc10ca801bd9332b517d2bac51d37622c68be8786b0be4985d",
    "HumanIntent": "0x93d1112706759e25621926fe3fe0b0f8491b1a831687d781c85f47297baf5275",
}
ASSESSMENT_ID = "0x4bfa40152304accb192d19ebd0347b888e357869a21f435472af34871279aa8d"
TYPE_HASHES = {
    "AI_ASSESSMENT_TYPEHASH": "0x47a0b10b5e80161cafb0697069c6548ef45c65a63508c85f768a865e998cd276",
    "RECIPIENT_RECEIPT_TYPEHASH": "0x8720256596b936b3fd7ce8eb653206cf471145e2e9a88da4d9bde8f216899ba4",
    "HUMAN_INTENT_TYPEHASH": "0x4340ae0f0e27730fbcd1f7e78d75c6aec31ad79110aba9dc16728473777802eb",
}


def fixture_views() -> tuple[dict, dict, dict]:
    project = {
        "projectId": b(1), "foundation": a(0x11), "recipient": a(0x22), "asset": a(0x33),
        "assetDecimals": 6, "policyEpoch": 7, "threshold": 2, "state": 0,
        "unresolvedProcurements": 1, "createdAt": 1_770_000_000,
    }
    procurement = {
        "procurementId": b(2), "projectId": b(1), "vendor": a(0x44), "budgetCap": 120_000_000,
        "poHash": b(5), "requestHash": b(6), "goodsRequestHash": b(7),
        "preEvidenceHash": GOLDEN["preEvidenceHash"], "preAssessmentId": b(8),
        "reservedAmount": 80_000_000, "invoiceHash": b(9), "invoiceAmount": 72_000_000,
        "goodsHash": b(10), "receiptDigest": b(11), "finalEvidenceHash": GOLDEN["finalEvidenceHash"],
        "finalAssessmentId": b(12), "conversionEvidenceHash": b(13), "paymentEvidenceHash": b(14),
        "settlementHash": b(15), "cancellationReasonHash": b(16), "returnedAmount": 0, "state": 6,
    }
    ledger = {
        "asset": a(0x33), "deposits": 100_000_000, "reserved": 80_000_000, "released": 0,
        "returned": 0, "refunded": 0, "refundPool": 0, "donorCount": 2,
        "claimedCount": 0, "policyEpoch": 7, "threshold": 2, "refundSnapshotted": False,
    }
    return project, procurement, ledger


def fixture_typed(primary: str) -> dict:
    messages = {
        "AIAssessment": {
            "stage": 0, "procurementId": b(2), "assessmentId": ASSESSMENT_ID, "outcome": 1,
            "riskScoreBps": 2750, "evidenceHash": GOLDEN["preEvidenceHash"], "reportHash": b(18),
            "signer": a(0x55), "nonce": 17, "deadline": 1_900_000_000,
        },
        "RecipientReceipt": {
            "projectId": b(1), "procurementId": b(2), "expectedRecipient": a(0x22),
            "vendor": a(0x44), "poHash": b(5), "invoiceHash": b(9), "invoiceAmount": 72_000_000,
            "goodsHash": b(10), "receiptEvidenceHash": b(19), "nonce": 23, "deadline": 1_900_000_040,
        },
        "HumanIntent": {
            "targetId": b(2), "action": 0, "termsHash": GOLDEN["reserveTermsHash"],
            "assessmentId": b(8), "signer": a(0x66), "nonce": 29,
            "deadline": 1_900_000_080, "policyEpoch": 7,
        },
    }
    return {
        "types": {
            "EIP712Domain": [{"name": name, "type": kind} for name, kind in oracle.DOMAIN_FIELDS],
            primary: [{"name": name, "type": kind} for name, kind in oracle.FIELDS[primary]],
        },
        "primaryType": primary,
        "domain": {"name": oracle.ESCROW if primary == "HumanIntent" else oracle.REGISTRY,
                   "version": "2", "chainId": 31337,
                   "verifyingContract": a(0x88 if primary == "HumanIntent" else 0x77)},
        "message": messages[primary],
    }


class FakeReadOnlyGateway:
    """Fixed fake ABI results; cannot deploy, transact, sign or connect to RPC."""

    def __init__(self):
        self.project, self.procurement, self.ledger = fixture_views()
        self.w3 = SimpleNamespace(eth=SimpleNamespace(block_number=456, chain_id=31337))
        self.calls = []
        self.verified = False
        self.bad_helper = False

    def verify(self):
        self.verified = True

    def contract_address(self, name):
        return a(0x77 if name == oracle.REGISTRY else 0x88)

    def call(self, contract, function, *args, block_identifier=None):
        if not self.verified:
            raise AssertionError("Getter before deployment gate")
        self.calls.append((contract, function, args, block_identifier))
        fixed = {
            "getProject": self.project, "getProcurement": self.procurement, "getLedger": self.ledger,
            "computePreEvidenceHash": GOLDEN["preEvidenceHash"],
            "computeFinalEvidenceHash": GOLDEN["finalEvidenceHash"],
            "reserveTermsHash": GOLDEN["reserveTermsHash"], "releaseTermsHash": GOLDEN["releaseTermsHash"],
            "settlementTermsHash": GOLDEN["settlementTermsHash"],
            "cancellationTermsHash": GOLDEN["cancellationTermsHash"], "closeTermsHash": GOLDEN["closeTermsHash"],
            "computeAssessmentId": ASSESSMENT_ID, "assessmentDigest": DIGESTS["AIAssessment"],
            "intentDigest": DIGESTS["HumanIntent"], **TYPE_HASHES,
        }
        if function not in fixed:
            raise AssertionError("Unknown or mutating method requested: " + function)
        if self.bad_helper and function == "reserveTermsHash":
            return b(99)
        return copy.deepcopy(fixed[function])


class ReferenceFormulaTests(unittest.TestCase):
    def test_all_nonzero_evidence_and_terms_golden_vectors(self):
        self.assertEqual(oracle.compute_view_hashes(*fixture_views(), 80_000_000), GOLDEN)

    def test_assessment_id_golden_and_excludes_self(self):
        message = fixture_typed("AIAssessment")["message"]
        self.assertEqual(oracle.compute_assessment_id(message), ASSESSMENT_ID)
        message["assessmentId"] = b(99)
        self.assertEqual(oracle.compute_assessment_id(message), ASSESSMENT_ID)

    def test_ai_digest_golden(self):
        self.assertEqual(oracle.compute_typed_digest(fixture_typed("AIAssessment")), DIGESTS["AIAssessment"])

    def test_recipient_digest_golden(self):
        self.assertEqual(oracle.compute_typed_digest(fixture_typed("RecipientReceipt")), DIGESTS["RecipientReceipt"])

    def test_human_digest_golden(self):
        self.assertEqual(oracle.compute_typed_digest(fixture_typed("HumanIntent")), DIGESTS["HumanIntent"])

    def test_every_message_field_is_bound(self):
        for primary, fields in oracle.FIELDS.items():
            for field, kind in fields:
                with self.subTest(primary=primary, field=field):
                    typed = fixture_typed(primary)
                    old = typed["message"][field]
                    typed["message"][field] = (old + 1 if kind.startswith("uint")
                                               else a(0x99) if kind == "address" else b(99))
                    self.assertNotEqual(oracle.compute_typed_digest(typed), DIGESTS[primary])

    def test_domain_chain_and_contract_are_bound(self):
        for primary in oracle.FIELDS:
            for field, value in (("chainId", 31338), ("verifyingContract", a(0x99))):
                with self.subTest(primary=primary, field=field):
                    typed = fixture_typed(primary)
                    typed["domain"][field] = value
                    self.assertNotEqual(oracle.compute_typed_digest(typed), DIGESTS[primary])

    def test_close_binds_actual_state_not_assumed_closing(self):
        p, q, ledger = fixture_views()
        results = set()
        for state in range(4):
            p["state"] = state
            results.add(oracle.compute_view_hashes(p, q, ledger, 80_000_000)["closeTermsHash"])
        self.assertEqual(len(results), 4)
        self.assertIn(GOLDEN["closeTermsHash"], results)

    def test_view_fields_and_amounts_are_bound(self):
        changes = [
            (0, "foundation", a(0x99), "preEvidenceHash"),
            (0, "recipient", a(0x99), "finalEvidenceHash"),
            (0, "unresolvedProcurements", 2, "closeTermsHash"),
            (1, "vendor", a(0x99), "reserveTermsHash"),
            (1, "budgetCap", 121_000_000, "preEvidenceHash"),
            (1, "poHash", b(99), "preEvidenceHash"),
            (1, "requestHash", b(99), "preEvidenceHash"),
            (1, "goodsRequestHash", b(99), "preEvidenceHash"),
            (1, "preAssessmentId", b(99), "reserveTermsHash"),
            (1, "reservedAmount", 81_000_000, "releaseTermsHash"),
            (1, "invoiceHash", b(99), "finalEvidenceHash"),
            (1, "invoiceAmount", 71_000_000, "finalEvidenceHash"),
            (1, "goodsHash", b(99), "finalEvidenceHash"),
            (1, "receiptDigest", b(99), "finalEvidenceHash"),
            (1, "finalAssessmentId", b(99), "releaseTermsHash"),
            (1, "settlementHash", b(99), "settlementTermsHash"),
            (1, "cancellationReasonHash", b(99), "cancellationTermsHash"),
            (2, "deposits", 101_000_000, "closeTermsHash"),
            (2, "donorCount", 3, "closeTermsHash"),
            (2, "reserved", 81_000_000, "closeTermsHash"),
        ]
        for index, field, value, affected in changes:
            with self.subTest(field=field):
                views = list(fixture_views())
                views[index][field] = value
                self.assertNotEqual(oracle.compute_view_hashes(*views, 80_000_000)[affected], GOLDEN[affected])
        self.assertNotEqual(oracle.compute_view_hashes(*fixture_views(), 79_000_000)["reserveTermsHash"],
                            GOLDEN["reserveTermsHash"])


class InputRejectionTests(unittest.TestCase):
    def test_wrong_field_order_width_and_extra_type_rejected(self):
        for mutation in ("order", "width", "extra"):
            with self.subTest(mutation=mutation):
                typed = fixture_typed("AIAssessment")
                if mutation == "order":
                    typed["types"]["AIAssessment"].reverse()
                elif mutation == "width":
                    typed["types"]["AIAssessment"][0]["type"] = "uint256"
                else:
                    typed["types"]["Anything"] = []
                with self.assertRaises(oracle.OracleMismatch):
                    oracle.compute_typed_digest(typed)

    def test_wrong_domain_name_and_version_rejected(self):
        for field, value in (("name", "PoGRegistry"), ("name", "ProcurementEscrowV2"), ("version", "1")):
            with self.subTest(field=field, value=value):
                typed = fixture_typed("RecipientReceipt")
                typed["domain"][field] = value
                with self.assertRaises(oracle.OracleMismatch):
                    oracle.compute_typed_digest(typed)

    def test_uint_width_boolean_float_string_and_negative_rejected(self):
        for primary in oracle.FIELDS:
            for field, kind in oracle.FIELDS[primary]:
                if not kind.startswith("uint"):
                    continue
                for value in (True, 1.0, "1", -1, 2**int(kind[4:])):
                    with self.subTest(primary=primary, field=field, value=value):
                        typed = fixture_typed(primary)
                        typed["message"][field] = value
                        with self.assertRaises(oracle.OracleMismatch):
                            oracle.compute_typed_digest(typed)

    def test_bad_hex_address_or_hash_rejected(self):
        for field, value in (("reportHash", "0x00"), ("reportHash", "0x" + "zz" * 32),
                             ("signer", "0x" + "ab" * 19), ("signer", "0x" + "gg" * 20)):
            with self.subTest(field=field, value=value):
                typed = fixture_typed("AIAssessment")
                typed["message"][field] = value
                with self.assertRaises(oracle.OracleMismatch):
                    oracle.compute_typed_digest(typed)

    def test_missing_or_extra_message_and_envelope_fields_rejected(self):
        for mutation in ("missing", "extra", "envelope"):
            typed = fixture_typed("HumanIntent")
            if mutation == "missing":
                del typed["message"]["nonce"]
            elif mutation == "extra":
                typed["message"]["unsignedAmount"] = 1
            else:
                typed["signature"] = "not allowed"
            with self.subTest(mutation=mutation), self.assertRaises(oracle.OracleMismatch):
                oracle.compute_typed_digest(typed)

    def test_mixed_views_and_ledger_underflow_rejected(self):
        for mutation in ("project", "asset", "epoch", "returned", "refund"):
            p, q, ledger = fixture_views()
            if mutation == "project":
                q["projectId"] = b(99)
            elif mutation == "asset":
                ledger["asset"] = a(0x99)
            elif mutation == "epoch":
                ledger["policyEpoch"] += 1
            elif mutation == "returned":
                ledger["returned"] = 1
            else:
                ledger["refunded"] = ledger["deposits"] + 1
            with self.subTest(mutation=mutation), self.assertRaises(oracle.OracleMismatch):
                oracle.compute_view_hashes(p, q, ledger, 80_000_000)


class FakeGetterProofTests(unittest.TestCase):
    def test_pinned_read_only_view_proof(self):
        gateway = FakeReadOnlyGateway()
        proof = oracle.check_views(gateway, b(1), b(2), 80_000_000)
        self.assertEqual(len(proof["checks"]), 9)
        self.assertEqual(proof["projectState"], 0)
        self.assertEqual(proof["procurementState"], 6)
        self.assertEqual({item[3] for item in gateway.calls}, {456})
        self.assertTrue(all(row["matches"] for row in proof["checks"]))

    def test_tuple_abi_views_accepted(self):
        gateway = FakeReadOnlyGateway()
        gateway.project = tuple(gateway.project[name] for name, _ in oracle.PROJECT_FIELDS)
        gateway.procurement = tuple(gateway.procurement[name] for name, _ in oracle.PROCUREMENT_FIELDS)
        gateway.ledger = tuple(gateway.ledger[name] for name, _ in oracle.LEDGER_FIELDS)
        self.assertEqual(len(oracle.check_views(gateway, b(1), b(2), 80_000_000)["checks"]), 9)

    def test_helper_disagreement_fails_closed(self):
        gateway = FakeReadOnlyGateway()
        gateway.bad_helper = True
        with self.assertRaisesRegex(oracle.OracleMismatch, "reserveTermsHash disagrees"):
            oracle.check_views(gateway, b(1), b(2), 80_000_000)

    def test_ai_and_human_digest_helper_proofs(self):
        for primary, expected_rows in (("AIAssessment", 5), ("HumanIntent", 3)):
            with self.subTest(primary=primary):
                gateway = FakeReadOnlyGateway()
                proof = oracle.check_typed(gateway, fixture_typed(primary), DIGESTS[primary])
                self.assertEqual(len(proof["checks"]), expected_rows)
                self.assertEqual({item[3] for item in gateway.calls}, {456})

    def test_receipt_before_confirmation_is_not_helper_proof(self):
        gateway = FakeReadOnlyGateway()
        gateway.procurement["receiptDigest"] = oracle.ZERO_HASH
        typed = fixture_typed("RecipientReceipt")
        proof = oracle.check_typed(gateway, typed, DIGESTS["RecipientReceipt"])
        self.assertFalse(proof["onChainReceiptCompared"])
        self.assertEqual(len(proof["checks"]), 2)
        self.assertIn("not yet accepted", proof["limitations"][0])
        with self.assertRaisesRegex(oracle.OracleMismatch, "No accepted receiptDigest"):
            oracle.check_typed(gateway, typed, DIGESTS["RecipientReceipt"], require_receipt_onchain=True)

    def test_receipt_after_confirmation_matches_stored_digest(self):
        gateway = FakeReadOnlyGateway()
        gateway.procurement["receiptDigest"] = DIGESTS["RecipientReceipt"]
        proof = oracle.check_typed(gateway, fixture_typed("RecipientReceipt"), DIGESTS["RecipientReceipt"],
                                   require_receipt_onchain=True)
        self.assertTrue(proof["onChainReceiptCompared"])
        self.assertEqual(proof["checks"][-1]["source"], "Registry.getProcurement.receiptDigest")

    def test_receipt_cannot_attest_wrong_current_goods(self):
        gateway = FakeReadOnlyGateway()
        gateway.procurement["receiptDigest"] = oracle.ZERO_HASH
        typed = fixture_typed("RecipientReceipt")
        typed["message"]["goodsHash"] = b(99)
        with self.assertRaisesRegex(oracle.OracleMismatch, "goodsHash differs"):
            oracle.check_typed(gateway, typed, oracle.compute_typed_digest(typed))

    def test_wrong_actual_chain_or_contract_rejected(self):
        for field, value in (("chainId", 31338), ("verifyingContract", a(0x99))):
            with self.subTest(field=field):
                gateway = FakeReadOnlyGateway()
                typed = fixture_typed("HumanIntent")
                typed["domain"][field] = value
                with self.assertRaises(oracle.OracleMismatch):
                    oracle.check_typed(gateway, typed, oracle.compute_typed_digest(typed))

    def test_wrong_api_digest_rejected(self):
        with self.assertRaisesRegex(oracle.OracleMismatch, "typedDigest disagrees"):
            oracle.check_typed(FakeReadOnlyGateway(), fixture_typed("HumanIntent"), b(99))

    def test_receipt_checkpoint_zero_late_fields_reported(self):
        gateway = FakeReadOnlyGateway()
        for name in ("finalAssessmentId", "conversionEvidenceHash", "paymentEvidenceHash",
                     "settlementHash", "cancellationReasonHash"):
            gateway.procurement[name] = oracle.ZERO_HASH
        # Formula-only test avoids falsely pretending unchanged fake helper values
        # are real ReceiptConfirmed helpers after changing these inputs.
        hashes = oracle.compute_view_hashes(gateway.project, gateway.procurement, gateway.ledger, 80_000_000)
        self.assertNotEqual(hashes["releaseTermsHash"], GOLDEN["releaseTermsHash"])
        self.assertNotEqual(hashes["settlementTermsHash"], GOLDEN["settlementTermsHash"])
        self.assertNotEqual(hashes["cancellationTermsHash"], GOLDEN["cancellationTermsHash"])
        self.assertEqual(hashes["closeTermsHash"], GOLDEN["closeTermsHash"])


class RealAddressABIEncodingTests(unittest.TestCase):
    def test_real_manifest_addresses_encode_in_accepted_getter_abis(self):
        import json
        from web3 import Web3
        root = Path(__file__).resolve().parents[1]
        for primary, contract, function, signer in (
            ("AIAssessment", "PoGRegistryV2", "assessmentDigest", "0x90f79bf6eb2c4f870365e785982e1f101e93b906"),
            ("HumanIntent", "ProcurementEscrowV2", "intentDigest", "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65"),
        ):
            typed = fixture_typed(primary)
            typed["message"]["signer"] = signer
            values = tuple(oracle._message(typed["message"], oracle.FIELDS[primary]))
            abi = json.loads((root / f"packages/contract-abis/v2/{contract}.json").read_text())
            offline = Web3().eth.contract(abi=abi)
            # No HTTP provider: exercise the real SDK encoder without any RPC.
            encoded = getattr(offline.functions, function)(values)._encode_transaction_data()
            self.assertTrue(encoded.startswith("0x") and len(encoded) > 10)

    def test_checksum_normalization_does_not_change_hash_bytes(self):
        from eth_utils import to_checksum_address
        typed = fixture_typed("AIAssessment")
        lower = "0x90f79bf6eb2c4f870365e785982e1f101e93b906"
        typed["message"]["signer"] = lower
        digest = oracle.compute_typed_digest(typed)
        typed["message"]["signer"] = to_checksum_address(lower)
        self.assertEqual(oracle.compute_typed_digest(typed), digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
