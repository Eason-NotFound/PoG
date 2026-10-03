"""M3.2 independent HTTP client; NOT a second backend or service adapter.

Only the owning runner starts/seeds/resets its isolated official API/PG/chain.
This client submits synthetic evidence and stops at ReceiptConfirmed. Passwords,
tokens and signatures stay in memory. No files, migrations or processes are
created here. Public evidence and the non-serializable ResetContext are separate.
Local implementation authorized by the human message '跟ceo联合完成m3.2吧'
in PM turn 01a100da-9c41-72c1-af99-79be064b395b; no GitHub publication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import json
import os
import re
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse


class ScenarioError(RuntimeError):
    """Fail closed, without echoing credentials or response bodies."""


AI_FIELDS = (("stage", "uint8"), ("procurementId", "bytes32"), ("assessmentId", "bytes32"),
             ("outcome", "uint8"), ("riskScoreBps", "uint16"), ("evidenceHash", "bytes32"),
             ("reportHash", "bytes32"), ("signer", "address"), ("nonce", "uint256"), ("deadline", "uint64"))
HUMAN_FIELDS = (("targetId", "bytes32"), ("action", "uint8"), ("termsHash", "bytes32"),
                ("assessmentId", "bytes32"), ("signer", "address"), ("nonce", "uint256"),
                ("deadline", "uint64"), ("policyEpoch", "uint32"))
RECEIPT_FIELDS = (("projectId", "bytes32"), ("procurementId", "bytes32"), ("expectedRecipient", "address"),
                  ("vendor", "address"), ("poHash", "bytes32"), ("invoiceHash", "bytes32"),
                  ("invoiceAmount", "uint256"), ("goodsHash", "bytes32"), ("receiptEvidenceHash", "bytes32"),
                  ("nonce", "uint256"), ("deadline", "uint64"))
DOMAIN_FIELDS = (("name", "string"), ("version", "string"), ("chainId", "uint256"), ("verifyingContract", "address"))
ACCOUNTS = (("foundation", "foundation", "foundation"), ("recipient", "recipient", "recipient"),
            ("donor", "donor", "donorA"), ("admin", "human_approver", "humanApprover"),
            ("service-ai-fixture", "service_ai", "aiSigner"), ("donor-fixture-b", "donor", "donorB"))
SIGNING = {
    "ai_pre": ("AIAssessment", "PoGRegistryV2", AI_FIELDS, "service-ai-fixture", "aiSigner"),
    "reserve": ("HumanIntent", "ProcurementEscrowV2", HUMAN_FIELDS, "admin", "humanApprover"),
    "receipt": ("RecipientReceipt", "PoGRegistryV2", RECEIPT_FIELDS, "recipient", "recipient"),
}


def _require(condition, message):
    if not condition:
        raise ScenarioError(message)


def _loopback_url(value):
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        raise ScenarioError("Malformed loopback API URL") from None
    _require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and port is not None
             and 1024 <= port <= 65535 and value == f"http://127.0.0.1:{port}",
             "Scenario requires literal credential-free loopback HTTP")
    return value


def _official(api_source):
    source = (Path(api_source).resolve() / "services/api/src").resolve()
    _require((source / "pog_api/chain.py").is_file(), "Official candidate source missing")
    sys.path.insert(0, str(source))
    import pog_api.chain as chain
    import pog_api.db as db
    import pog_api.worker as worker
    import pog_api.test_database as safety
    for module in (chain, db, worker, safety):
        _require(Path(module.__file__).resolve().is_relative_to(source),
                 "Already-imported API module is not from the pinned source")
    return chain.LocalChainGateway, db.build_engine, db.build_session_factory, worker.ChainWorker, worker.ChainIndexer, safety.assert_safe_test_target


def _database_guard(value, safe_target):
    # No group/default connection, and guard runs BEFORE any SQL connection.
    _require(value == os.environ.get("POG_M3_2_SCENARIO_DATABASE_URL")
             and value == os.environ.get("POG_TEST_DATABASE_URL")
             and bool(os.environ.get("POG_MANAGED_POSTGRES_STATE")),
             "Exact owned PostgreSQL grant/marker missing")
    safe_target(value)


def _owned_context(base_url, manifest_path, artifact_dir):
    """Reject accidental direct use against an existing team/default service."""
    root = Path(artifact_dir).resolve()
    manifest_path = Path(manifest_path)
    _require(not manifest_path.is_symlink() and manifest_path.resolve() == root / "chain/manifest.json",
             "Scenario manifest is not in its exact owned run directory")
    _require(root.is_relative_to(Path("/tmp").resolve()) and root.name.startswith("pog-local-m3-2-")
             and (root.stat().st_mode & 0o777) == 0o700,
             "Scenario requires a private owned temporary run")
    owner_path = root / "owner.json"
    _require(not owner_path.is_symlink(), "Owner marker cannot be a symlink")
    try:
        owner = json.loads(owner_path.read_text())
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        raise ScenarioError("Owned marker/manifest is missing or malformed") from None
    _require(isinstance(owner, dict) and isinstance(manifest, dict), "Owned records must be JSON objects")
    _require(isinstance(owner.get("invocation"), str)
             and re.fullmatch(r"[0-9a-f]{32}", owner["invocation"]) is not None
             and owner["invocation"] == os.environ.get("POG_M3_2_INVOCATION")
             and isinstance(owner.get("sourceCandidate"), str)
             and re.fullmatch(r"[0-9a-f]{40}", owner["sourceCandidate"]) is not None
             and owner["sourceCandidate"] == os.environ.get("POG_M3_2_API_SHA"),
             "Owned invocation/candidate grant is missing or inconsistent")
    ports = owner.get("ports", [])
    _require(owner.get("owner") == "pog-blockchain-m3-2" and len(ports) == 3
             and all(type(port) is int and 1024 <= port <= 65535
                     and port not in (8545, 18545, 55433) for port in ports)
             and len(set(ports)) == 3, "Scenario targets are not independently owned ports")
    _require(base_url == f"http://127.0.0.1:{ports[2]}"
             and manifest["chain"]["rpcUrl"] == f"http://127.0.0.1:{ports[1]}"
             and Path(os.environ.get("POG_MANAGED_POSTGRES_STATE", "")).resolve() == root / "pg",
             "HTTP/RPC/PostgreSQL do not belong to the same owned invocation")
    try:
        pg_marker = json.loads((root / "pg/managed.json").read_text())
        pg_url = urlparse(os.environ.get("POG_TEST_DATABASE_URL", ""))
        actual_pg_port = pg_url.port
    except (OSError, ValueError):
        raise ScenarioError("Owned PostgreSQL marker/URL is malformed") from None
    _require(pg_marker.get("host") == pg_url.hostname == "127.0.0.1"
             and pg_marker.get("port") == actual_pg_port == ports[0],
             "Actual PostgreSQL URL/marker is not the owned invocation port")


def _hex(value):
    return value.lower() if isinstance(value, str) else "0x" + bytes(value).hex()


def _auth(token, key=None):
    result = {"Authorization": f"Bearer {token}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def _response(response, expected, label):
    # Never use response.text: an error may echo credentials/signatures.
    _require(response.status_code == expected,
             f"{label}: expected HTTP {expected}, got {response.status_code}")
    try:
        value = response.json()
    except ValueError:
        raise ScenarioError(f"{label}: response is not JSON") from None
    _require(isinstance(value, dict), f"{label}: response is not a JSON object")
    return value


def _resource_binding(operation, operation_id, expected_resource):
    _require(isinstance(expected_resource, tuple) and len(expected_resource) == 2,
             "Independent expected API resource binding is missing")
    resource_type, resource_id = expected_resource
    _require(operation.get("operationId") == operation_id and operation.get("resourceType") == resource_type
             and operation.get("resourceId") == resource_id, "Operation ID or API resource UUID binding mismatch")


def _login(client, manifest):
    password = os.environ.get("POG_M3_2_DEMO_PASSWORD", "")
    _require(len(password) >= 12, "Owner has not supplied the demo password")
    tokens, ids = {}, {}
    for username, role, wallet_role in ACCOUNTS:
        value = _response(client.post("/v2/sessions", json={"username": username, "password": password}),
                          200, f"login {username}")
        user = value["user"]
        _require(user["username"] == username and user["role"] == role,
                 "Seed identity does not have its fixed application role")
        _require(user["walletAddress"].lower() == manifest["roles"][wallet_role].lower(),
                 "Seed wallet does not match the accepted manifest role")
        tokens[username], ids[username] = value["token"], user["id"]
    _require(len({x.lower() for x in manifest["roles"].values()}) == len(manifest["roles"]),
             "Role wallets are not independent")
    return tokens, ids


def _pdf(label):
    from pypdf import PdfWriter
    output, writer = BytesIO(), PdfWriter()
    writer.add_blank_page(width=144, height=144)
    writer.add_metadata({"/Title": label, "/Subject": "Synthetic M3.2 test; no real evidence"})
    writer.write(output)
    return output.getvalue()


def _upload(client, token, procurement_id, category, key):
    return _response(client.post("/v2/documents", data={"procurementId": procurement_id, "category": category},
                                files={"file": (f"synthetic-{category}.pdf", _pdf(category), "application/pdf")},
                                headers=_auth(token, key)), 202, f"upload {category}")["document"]


def _typed_shape(kind, request, gateway):
    primary, contract, fields, _username, role = SIGNING[kind]
    data = request["typedData"]
    _require(data["primaryType"] == primary and data["domain"] == {
        "name": contract, "version": "2", "chainId": 31337,
        "verifyingContract": gateway.contract_address(contract),
    }, f"{kind}: frozen domain mismatch")
    _require(data["types"] == {
        "EIP712Domain": [{"name": n, "type": t} for n, t in DOMAIN_FIELDS],
        primary: [{"name": n, "type": t} for n, t in fields],
    }, f"{kind}: frozen types/widths/order mismatch")
    _require(set(data["message"]) == {n for n, _ in fields}, f"{kind}: unexpected message fields")
    signer_field = "expectedRecipient" if kind == "receipt" else "signer"
    _require(data["message"][signer_field].lower() == gateway.roles[role].lower()
             and request["signer"].lower() == gateway.roles[role].lower(), f"{kind}: independent signer mismatch")
    _require(int(request["nonce"]) == int(data["message"]["nonce"])
             and int(request["deadline"]) == int(data["message"]["deadline"]), f"{kind}: nonce/deadline mismatch")
    _require(request["synthetic"] is (kind == "ai_pre"), f"{kind}: synthetic label mismatch")
    return data


def _digest_check(kind, request, gateway):
    from m3_2_hash_oracle import check_typed
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak
    data = _typed_shape(kind, request, gateway)
    oracle = check_typed(gateway, data, request["digest"])
    signable = encode_typed_data(full_message=data)
    computed = "0x" + keccak(b"\x19" + signable.version + signable.header + signable.body).hex()
    _require(computed == request["digest"].lower(), f"{kind}: independent EIP712 digest mismatch")
    m = data["message"]
    if kind == "ai_pre":
        values = [m[name] for name, _ in AI_FIELDS]
        _require(int(m["stage"]) == 0, "M3.2 fixture is not PRE")
        _require(_hex(gateway.call("PoGRegistryV2", "assessmentDigest", values)) == computed,
                 "AI digest differs from accepted helper")
        _require(_hex(gateway.call("PoGRegistryV2", "computeAssessmentId", values)) == m["assessmentId"].lower(),
                 "AI assessment ID differs from accepted helper")
        _require(_hex(gateway.call("PoGRegistryV2", "computePreEvidenceHash", m["procurementId"]))
                 == m["evidenceHash"].lower(), "AI PRE evidence differs from immutable chain evidence")
        method = "independent ABI/Keccak oracle + Solidity assessmentDigest/computeAssessmentId/computePreEvidenceHash; SDK encoder redundant"
    elif kind == "reserve":
        _require(int(m["action"]) == 0, "M3.2 human intent is not Reserve")
        _require(_hex(gateway.call("ProcurementEscrowV2", "intentDigest", [m[n] for n, _ in HUMAN_FIELDS]))
                 == computed, "Human digest differs from accepted helper")
        method = "independent ABI/Keccak oracle + Solidity intentDigest/reserveTermsHash; SDK encoder redundant"
    else:
        # Accepted Registry has no public Recipient receipt-digest helper.
        method = "independent ABI/Keccak oracle; actual signature acceptance + stored/event receiptDigest; SDK encoder redundant"
    return {"kind": kind, "requestId": request["id"], "signer": request["signer"], "digest": computed,
            "nonce": str(m["nonce"]), "deadline": str(m["deadline"]), "domain": data["domain"],
            "synthetic": request["synthetic"], "verification": method, "independentOracle": oracle}


@dataclass(repr=False)
class ResetContext:
    project_id: str
    procurement_id: str
    receipt_request_id: str
    pending_operation_id: str
    old_run_id: str
    old_instance_id: str
    receipt_signature: str = field(repr=False)
    document_id: str = ""
    document_version_id: str = ""


class _Scenario:
    def __init__(self, client, tokens, gateway, worker, indexer, factory=None):
        self.client, self.tokens, self.gateway = client, tokens, gateway
        self.worker, self.indexer = worker, indexer
        self.operations, self.signing = [], []
        self.project_business_id = None
        self.factory = factory

    def post(self, path, body, username, key):
        return _response(self.client.post(path, json=body, headers=_auth(self.tokens[username], key)), 202, key)

    def get(self, path, username):
        return _response(self.client.get(path, headers=_auth(self.tokens[username])), 200, path)

    def queued(self, value, username, project_business_id=None, procurement_business_id=None, expected_amount=None, expected_resource=None):
        project_business_id = project_business_id or self.project_business_id
        operation = value["operation"]
        _require(operation["status"] == "queued" and operation["chainVerified"] is False,
                 "HTTP 202 must remain queued until canonical confirmation")
        operation_id = operation["operationId"]
        _resource_binding(operation, operation_id, expected_resource)
        for _ in range(100):
            self.worker.once()
            self.indexer.once()
            fact = self.get(f"/v2/operations/{operation_id}", username)
            _resource_binding(fact, operation_id, expected_resource)
            if fact["status"] == "confirmed":
                break
            _require(fact["status"] not in {"failed", "requires_attention", "invalidated_instance"},
                     "Official worker/indexer rejected a queued operation")
        else:
            raise ScenarioError("Operation did not confirm within 100 bounded drain ticks")
        _require(fact["chainVerified"] is True and fact.get("steps"), "Confirmed operation lacks chain facts")
        summary = {"operationId": operation_id, "principal": username, "queuedHttpStatus": 202,
                   "confirmedHttpStatus": 200, "operationKind": fact["operationKind"], "status": "confirmed", "steps": [],
                   "resourceType": expected_resource[0], "resourceId": expected_resource[1]}
        for step in fact["steps"]:
            tx = step["transaction"]
            _require(step["status"] == tx["status"] == "confirmed" and tx["receiptStatus"] == 1
                     and tx["canonical"] is True, "Step is not status-1 canonical-confirmed")
            receipt, events = self.gateway.receipt_with_events(tx["transactionHash"], step["expectedEvent"])
            _require(receipt is not None and receipt["status"] == 1 and bool(events), "RPC receipt/event mismatch")
            _require(receipt["blockNumber"] == tx["blockNumber"] and receipt["blockHash"].lower() == tx["blockHash"].lower(),
                     "API block facts differ from RPC receipt")
            _require(_hex(self.gateway.w3.eth.get_block(receipt["blockNumber"])["hash"]) == receipt["blockHash"].lower(),
                     "Receipt block is no longer canonical")
            action = step["action"]
            _require(step["expectedEvent"] == {
                "project.create": "ProjectCreated", "donation.approve": "Approval", "donation.deposit": "Donated",
                "procurement.create": "ProcurementCreated", "procurement.po": "PurchaseOrderRecorded",
                "assessment.ai_pre": "AIAssessmentRecorded", "approval.reserve": "HumanApprovalSubmitted",
                "reserve.execute": "BudgetReserved", "procurement.invoice": "InvoiceAndGoodsRecorded",
                "receipt.submit": "RecipientReceiptAccepted",
            }.get(action), "Action does not have its frozen expected event")
            contract = {
                "project.create": "PoGRegistryV2", "donation.approve": "MockHKD", "donation.deposit": "ProcurementEscrowV2",
                "procurement.create": "PoGRegistryV2", "procurement.po": "PoGRegistryV2", "assessment.ai_pre": "PoGRegistryV2",
                "approval.reserve": "ProcurementEscrowV2", "reserve.execute": "ProcurementEscrowV2",
                "procurement.invoice": "PoGRegistryV2", "receipt.submit": "PoGRegistryV2",
            }.get(action)
            _require(contract is not None, "Unexpected action exceeds the M3.2 checkpoint scope")
            role = "relayer" if action in {"assessment.ai_pre", "approval.reserve", "receipt.submit"} else dict(
                (name, manifest_role) for name, _application_role, manifest_role in ACCOUNTS)[username]
            _require(receipt["from"].lower() == self.gateway.roles[role].lower()
                     and receipt["to"].lower() == self.gateway.contract_address(contract).lower(),
                     "Receipt caller or destination differs from the fixed action authority")
            for event in events:
                args = event["args"]
                _require(event["address"].lower() == self.gateway.contract_address(contract).lower(),
                         "Expected event was emitted by the wrong contract")
                if project_business_id and "projectId" in args:
                    _require(args["projectId"].lower() == project_business_id.lower(), "Event project binding mismatch")
                if procurement_business_id and "procurementId" in args:
                    _require(args["procurementId"].lower() == procurement_business_id.lower(), "Event procurement binding mismatch")
                if procurement_business_id and "targetId" in args:
                    _require(args["targetId"].lower() == procurement_business_id.lower(), "Human event target binding mismatch")
                amount_field = {"donation.approve": "value", "donation.deposit": "amount", "reserve.execute": "amount",
                                "procurement.invoice": "invoiceAmount", "procurement.create": "budgetCap"}.get(action)
                amount = expected_amount if action.startswith("donation.") else 72_000_000 if action == "procurement.invoice" else 80_000_000
                if amount_field:
                    _require(amount is not None and int(args[amount_field]) == amount, "Expected event amount mismatch")
                if action == "donation.approve":
                    _require(args["owner"].lower() == self.gateway.roles[role].lower()
                             and args["spender"].lower() == self.gateway.contract_address("ProcurementEscrowV2").lower(),
                             "Token Approval authority/escrow binding mismatch")
                if action == "donation.deposit":
                    _require(args["donor"].lower() == self.gateway.roles[role].lower(), "Donation credit event wrong donor")
            pinned = {"blockNumber": receipt["blockNumber"]}
            call = lambda name, function, *args: self.gateway.call(name, function, *args, block_identifier=receipt["blockNumber"])
            if action == "project.create":
                p = call("PoGRegistryV2", "getProject", project_business_id)
                _require(_hex(p[0]) == project_business_id.lower() and int(p[7]) == 0
                         and p[1].lower() == self.gateway.roles["foundation"].lower()
                         and p[2].lower() == self.gateway.roles["recipient"].lower(), "Receipt-block project facts mismatch")
                pinned["projectStateEnum"] = int(p[7])
            elif action == "donation.approve":
                _require(int(call("MockHKD", "allowance", self.gateway.roles[role], self.gateway.contract_address("ProcurementEscrowV2")))
                         == expected_amount, "Receipt-block approval does not match exact amount")
                pinned["approvedAmountAtomic"] = str(expected_amount)
            elif action == "donation.deposit":
                donor_credit = int(call("ProcurementEscrowV2", "donorCredit", project_business_id, self.gateway.roles[role]))
                _require(donor_credit == expected_amount, "Receipt-block donor credit mismatch")
                pinned["donorCreditAtomic"] = str(donor_credit)
            else:
                q = call("PoGRegistryV2", "getProcurement", procurement_business_id)
                expected_state = {"procurement.create": 0, "procurement.po": 1, "assessment.ai_pre": 2,
                                  "approval.reserve": 3, "reserve.execute": 4, "procurement.invoice": 5, "receipt.submit": 6}[action]
                _require(int(q[-1]) == expected_state and _hex(q[1]) == project_business_id.lower(), "Receipt-block procurement stage/project mismatch")
                if action in {"reserve.execute", "procurement.invoice", "receipt.submit"}:
                    _require(int(q[9]) == 80_000_000, "Receipt-block reserved amount mismatch")
                if action in {"procurement.invoice", "receipt.submit"}:
                    _require(int(q[11]) == 72_000_000, "Receipt-block invoice amount mismatch")
                if action == "receipt.submit":
                    _require(any(event["args"]["receiptDigest"].lower() == _hex(q[13]) for event in events),
                             "Receipt-block stored receipt digest does not match its event")
                if action == "assessment.ai_pre":
                    _require(any(event["args"]["assessmentId"].lower() == _hex(q[8])
                                 and event["args"]["evidenceHash"].lower() == _hex(q[7]) for event in events),
                             "Receipt-block current AI assessment differs from its event")
                pinned["procurementStateEnum"] = int(q[-1])
            _require(_hex(self.gateway.w3.eth.get_block(receipt["blockNumber"])["hash"]) == receipt["blockHash"].lower(),
                     "Canonical block changed during pinned getter verification")
            summary["steps"].append({"action": step["action"], "expectedEvent": step["expectedEvent"],
                                     "transactionHash": tx["transactionHash"], "blockNumber": tx["blockNumber"],
                                     "blockHash": tx["blockHash"], "receiptStatus": 1, "canonical": True,
                                     "eventLogIndexes": [event["logIndex"] for event in events],
                                     "eventContractAddresses": sorted({event["address"] for event in events}),
                                     "caller": receipt["from"], "destination": receipt["to"], "receiptBlockGetterFacts": pinned})
        self.operations.append(summary)
        return fact

    def sign_and_submit(self, procurement, kind, extra=None, suffix=""):
        _primary, _contract, _fields, username, role = SIGNING[kind]
        request = self.post(f"/v2/procurements/{procurement['id']}/signing-requests", {"kind": kind, **(extra or {})},
                            username, f"m3.2-main-{kind}{suffix}-prepare")["signingRequest"]
        _require(request["procurementId"] == procurement["id"] and request["kind"] == kind,
                 "Signing request does not target the exact procurement UUID/kind")
        _require(request["status"] == "prepared", "Request is not prepared")
        self.signing.append(_digest_check(kind, request, self.gateway))
        if kind == "reserve":
            _require(request["typedData"]["message"]["termsHash"].lower()
                     == _hex(self.gateway.call("ProcurementEscrowV2", "reserveTermsHash", procurement["businessId"], 80_000_000)),
                     "Human request does not bind exact 80 mHKD reserve terms")
        signed = self.post(f"/v2/signing-requests/{request['id']}/sign-demo", {"confirm": True}, username,
                           f"m3.2-main-{kind}{suffix}-authorize")
        _require(signed["signingRequest"]["status"] == "signed" and signed["signingRequest"]["id"] == request["id"],
                 "Explicit signer authorization did not sign the exact request")
        # API intentionally omits the signature in its response. Official local
        # gateway obtains submit bytes from that same unlocked signer, never a key.
        signature = self.gateway.sign_typed_data(self.gateway.roles[role], request["typedData"])
        # The official sign-demo route persists this field. Compare privately;
        # neither value nor a parameter containing signature enters evidence.
        from sqlalchemy import text
        _require(self.factory is not None, "Owned DB read factory missing for signature authorization check")
        with self.factory() as session:
            stored_signature = session.execute(text("SELECT signature FROM signing_requests WHERE id=:id"),
                                               {"id": request["id"]}).scalar_one()
        _require(isinstance(stored_signature, str) and signature.lower() == stored_signature.lower(),
                 "Official sign-demo stored signature differs from local gateway submit bytes")
        self.signing[-1]["demoSignatureMatchesPersisted"] = True
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        _require(Account.recover_message(encode_typed_data(full_message=request["typedData"]), signature=signature).lower()
                 == self.gateway.roles[role].lower(), "Independent EOA recovery mismatch")
        fact = self.queued(self.post(f"/v2/signing-requests/{request['id']}/submit", {"signature": signature}, username,
                                     f"m3.2-main-{kind}{suffix}-submit"), username, procurement_business_id=procurement["businessId"],
                           expected_resource=("signing_request", request["id"]))
        _require(self.get(f"/v2/signing-requests/{request['id']}", username)["status"] == "confirmed", "Signing record not confirmed")
        return request, signature, fact

    def renewal(self, procurement, old_ai, old_human, old_human_signature):
        """Same immutable PRE, genuinely consumed old human vote, no time warp."""
        from eth_abi import encode
        from eth_utils import keccak
        from web3.exceptions import ContractCustomError

        def bundle(message):
            return "0x" + keccak(encode(["bytes32", "uint8", "bytes32", "bytes32", "uint32"],
                                        [bytes.fromhex(message["targetId"][2:]), int(message["action"]),
                                         bytes.fromhex(message["termsHash"][2:]), bytes.fromhex(message["assessmentId"][2:]),
                                         int(message["policyEpoch"])] )).hex()

        qid, business_id = procurement["id"], procurement["businessId"]
        old_m = old_human["typedData"]["message"]
        old_bundle = bundle(old_m)
        old_deadline = int(self.gateway.call("ProcurementEscrowV2", "voteDeadline", old_bundle, self.gateway.roles["humanApprover"]))
        _require(int(self.gateway.call("PoGRegistryV2", "getProcurement", business_id)[-1]) == 3
                 and old_deadline == int(old_m["deadline"])
                 and int(self.gateway.call("ProcurementEscrowV2", "humanNonces", self.gateway.roles["humanApprover"])) == 1,
                 "Old human vote was not actually consumed on-chain at ReserveApprovalPending")
        renewed_ai, _signature, _fact = self.sign_and_submit(procurement, "ai_pre", suffix="-renewed")
        new_ai = renewed_ai["typedData"]["message"]
        old_ai_m = old_ai["typedData"]["message"]
        _require(new_ai["nonce"] == 1 and new_ai["assessmentId"] != old_ai_m["assessmentId"]
                 and new_ai["evidenceHash"] == old_ai_m["evidenceHash"]
                 and int(self.gateway.call("PoGRegistryV2", "getProcurement", business_id)[-1]) == 2,
                 "Same-evidence AI renewal did not replace the assessment and return to PreAssessed")
        new_terms = _hex(self.gateway.call("ProcurementEscrowV2", "reserveTermsHash", business_id, 80_000_000))
        new_bundle = bundle({**old_m, "termsHash": new_terms, "assessmentId": new_ai["assessmentId"]})
        _require(new_bundle != old_bundle and new_terms != old_m["termsHash"]
                 and int(self.gateway.call("ProcurementEscrowV2", "voteDeadline", new_bundle, self.gateway.roles["humanApprover"])) == 0,
                 "Renewed assessment unexpectedly retained an old human vote")
        before = (int(self.gateway.w3.eth.block_number),
                  int(self.gateway.w3.eth.get_transaction_count(self.gateway.roles["foundation"])),
                  int(self.gateway.w3.eth.get_transaction_count(self.gateway.roles["relayer"])))
        missing_vote = self.client.post(f"/v2/procurements/{qid}/chain/reserve", json={"reserveAmountAtomic": "80000000"},
                                        headers=_auth(self.tokens["foundation"], "m3.2-renewal-missing-current-vote"))
        _require(missing_vote.status_code == 409, "HTTP reserve accepted missing current human vote")
        old_resubmit = self.client.post(f"/v2/signing-requests/{old_human['id']}/submit", json={"signature": old_human_signature},
                                        headers=_auth(self.tokens["admin"], "m3.2-renewal-old-vote-resubmit"))
        _require(old_resubmit.status_code == 409, "Consumed old human signing request was reused as a fresh vote")
        try:
            self.gateway.contracts["ProcurementEscrowV2"].functions.executeReserve(business_id, 80_000_000).call(
                {"from": self.gateway.roles["foundation"]})
        except ContractCustomError as exc:
            data = getattr(exc, "data", None)
            if not isinstance(data, str):
                data = exc.args[0] if exc.args else ""
            expected = "0x" + keccak(text="InvalidState(uint8,uint8)")[:4].hex() + encode(["uint8", "uint8"], [3, 2]).hex()
            _require(isinstance(data, str) and data.lower() == expected.lower(), "Reserve eth_call did not reject at the actual InvalidState(3,2) boundary")
        else:
            raise ScenarioError("Reserve eth_call counted a stale human bundle after AI renewal")
        after = (int(self.gateway.w3.eth.block_number),
                 int(self.gateway.w3.eth.get_transaction_count(self.gateway.roles["foundation"])),
                 int(self.gateway.w3.eth.get_transaction_count(self.gateway.roles["relayer"])))
        _require(before == after, "Rejected renewal requests unexpectedly broadcast")
        fresh_human, _signature, _fact = self.sign_and_submit(procurement, "reserve", {"reserveAmountAtomic": "80000000"}, suffix="-renewed")
        fresh_m = fresh_human["typedData"]["message"]
        _require(int(fresh_m["nonce"]) == 1 and fresh_m["termsHash"].lower() == new_terms
                 and fresh_m["assessmentId"] == new_ai["assessmentId"]
                 and int(self.gateway.call("PoGRegistryV2", "getProcurement", business_id)[-1]) == 3,
                 "Fresh human nonce/current terms failed to form the new reserve bundle")
        return {"oldHumanActuallyOnChain": True, "oldState": 3, "afterAIRenewalState": 2, "afterFreshHumanState": 3,
                "oldAssessmentId": old_ai_m["assessmentId"], "newAssessmentId": new_ai["assessmentId"],
                "oldBundleKey": old_bundle, "newBundleKey": new_bundle, "oldVoteDeadline": str(old_deadline),
                "newBundleVoteDeadlineBeforeFreshHuman": "0", "freshHumanNonce": "1", "sameImmutableEvidence": True,
                "missingCurrentVoteHttpStatus": 409, "consumedOldVoteResubmitHttpStatus": 409,
                "reserveEthCallRejection": "InvalidState(3,2)", "rejectedRequestsBroadcast": False,
                "limitation": "State 2 is rejected before threshold counting; old vote remains historical under its old key, new bundle has no vote."}


def run_scenario(base_url: str, database_url: str, manifest_path: Path, api_source: Path,
                 artifact_dir: Path) -> tuple[dict[str, Any], ResetContext]:
    """HTTP D100/Q80/Invoice72 -> ReceiptConfirmed; then queue one unsent reset probe.

    The owner provides a fresh, pinned API/PG/chain and exclusively owns drain.
    No background worker must race this client. artifact_dir is context only;
    the owner writes public JSON evidence, never the ResetContext.
    """
    _loopback_url(base_url)
    _require(Path(artifact_dir).is_dir(), "Owned artifact directory missing")
    _owned_context(base_url, manifest_path, artifact_dir)
    import httpx
    from sqlalchemy import text
    Gateway, build_engine, build_factory, Worker, Indexer, safety = _official(api_source)
    _database_guard(database_url, safety)
    gateway = Gateway(Path(manifest_path), Path(api_source))
    engine = build_engine(database_url)
    factory = build_factory(engine)
    worker, indexer = Worker(database_url, gateway), Indexer(database_url, gateway)
    try:
        _require(gateway.call("PoGRegistryV2", "projectCount") == 0, "Refusing nonempty chain")
        with factory() as session:
            _require(session.execute(text("SELECT count(*) FROM projects")).scalar_one() == 0, "Refusing existing API projects")
        with httpx.Client(base_url=base_url, timeout=30, trust_env=False, follow_redirects=False) as client:
            tokens, ids = _login(client, gateway.manifest)
            s = _Scenario(client, tokens, gateway, worker, indexer, factory)
            project = s.post("/v2/projects", {"title": "M3.2 independent synthetic project", "publicSummary": "Local MVP; no real assets.",
                                             "recipientUserId": ids["recipient"], "humanApproverUserId": ids["admin"]},
                             "foundation", "m3.2-main-project-draft")["project"]
            project_path = f"/v2/projects/{project['id']}"
            s.project_business_id = project["businessId"]
            s.queued(s.post(project_path + "/chain/create", {}, "foundation", "m3.2-main-project-create"),
                     "foundation", project_business_id=project["businessId"], expected_resource=("project", project["id"]))
            for username, amount in (("donor", 60_000_000), ("donor-fixture-b", 40_000_000)):
                s.queued(s.post(project_path + "/donations", {"amountAtomic": str(amount)}, username, f"m3.2-main-{username}-donate"),
                         username, project_business_id=project["businessId"], expected_amount=amount, expected_resource=("project", project["id"]))
            procurement = s.post("/v2/procurements", {"projectId": project["id"], "title": "Synthetic kit", "vendorWallet": gateway.roles["vendor"],
                                                     "budgetCapAtomic": "80000000"}, "foundation", "m3.2-main-procurement-draft")["procurement"]
            proc_path = f"/v2/procurements/{procurement['id']}"
            s.queued(s.post(proc_path + "/chain/create", {}, "foundation", "m3.2-main-procurement-create"),
                     "foundation", procurement_business_id=procurement["businessId"], expected_resource=("procurement", procurement["id"]))
            po_body = {}
            for category, name in (("purchase_order", "poDocumentVersionId"), ("request", "requestDocumentVersionId"),
                                   ("goods_request", "goodsRequestDocumentVersionId")):
                po_body[name] = _upload(client, tokens["foundation"], procurement["id"], category, "m3.2-main-upload-" + category)["versionId"]
            po_queued = s.post(proc_path + "/chain/purchase-order", po_body, "foundation", "m3.2-main-po")
            _require(s.get(proc_path, "foundation")["chainState"]["verified"] is False, "Queued PO shown as verified")
            s.queued(po_queued, "foundation", procurement_business_id=procurement["businessId"], expected_resource=("procurement", procurement["id"]))
            old_ai, _, _ = s.sign_and_submit(procurement, "ai_pre")
            old_human, old_human_signature, _ = s.sign_and_submit(procurement, "reserve", {"reserveAmountAtomic": "80000000"})
            renewal = s.renewal(procurement, old_ai, old_human, old_human_signature)
            s.queued(s.post(proc_path + "/chain/reserve", {"reserveAmountAtomic": "80000000"}, "foundation", "m3.2-main-reserve-execute"),
                     "foundation", procurement_business_id=procurement["businessId"], expected_resource=("procurement", procurement["id"]))
            invoice = _upload(client, tokens["foundation"], procurement["id"], "invoice", "m3.2-main-upload-invoice")
            goods = _upload(client, tokens["foundation"], procurement["id"], "goods_evidence", "m3.2-main-upload-goods")
            s.queued(s.post(proc_path + "/chain/invoice-and-goods", {"invoiceDocumentVersionId": invoice["versionId"],
                         "goodsDocumentVersionId": goods["versionId"], "invoiceAmountAtomic": "72000000"}, "foundation", "m3.2-main-invoice"),
                     "foundation", procurement_business_id=procurement["businessId"], expected_resource=("procurement", procurement["id"]))
            receipt_doc = _upload(client, tokens["recipient"], procurement["id"], "receipt_evidence", "m3.2-main-upload-recipient-evidence")
            receipt_request, signature, receipt_fact = s.sign_and_submit(procurement, "receipt", {"receiptEvidenceDocumentVersionId": receipt_doc["versionId"]})
            view = s.get(proc_path, "foundation")
            _require(view["chainState"]["status"] == "receipt_confirmed" and view["chainState"]["verified"] is True, "API not ReceiptConfirmed")
            checkpoint_block = int(receipt_fact["steps"][0]["transaction"]["blockNumber"])
            checkpoint_hash = receipt_fact["steps"][0]["transaction"]["blockHash"].lower()
            call = lambda name, function, *args: gateway.call(name, function, *args, block_identifier=checkpoint_block)
            cp = call("PoGRegistryV2", "getProcurement", procurement["businessId"])
            _require(int(cp[-1]) == 6 and int(cp[9]) == 80_000_000 and int(cp[11]) == 72_000_000, "On-chain procurement checkpoint mismatch")
            receipt_digest = _hex(cp[13])
            _require(receipt_digest == receipt_request["digest"].lower(), "Actual accepted receipt digest mismatch")
            from m3_2_hash_oracle import check_typed, check_views
            receipt_oracle = check_typed(gateway, receipt_request["typedData"], receipt_request["digest"],
                                         require_receipt_onchain=True, block_identifier=checkpoint_block)
            view_oracle = check_views(gateway, project["businessId"], procurement["businessId"], 80_000_000, block_identifier=checkpoint_block)
            receipt_tx = receipt_fact["steps"][0]["transaction"]["transactionHash"]
            _, receipt_events = gateway.receipt_with_events(receipt_tx, "RecipientReceiptAccepted")
            _require(any(e["args"].get("receiptDigest", "").lower() == receipt_digest for e in receipt_events), "Accepted receipt event digest mismatch")
            ledgers = {name: s.get(project_path + "/ledger", name) for name in ("foundation", "donor", "donor-fixture-b")}
            for ledger in ledgers.values():
                _require(ledger["chainVerified"] is True and ledger["depositsAtomic"] == "100000000" and ledger["reservedAtomic"] == "80000000"
                         and ledger["releasedAtomic"] == ledger["returnedAtomic"] == ledger["refundedAtomic"] == "0", "API ledger checkpoint mismatch")
            _require(ledgers["donor"]["currentCallerDonorCreditAtomic"] == "60000000" and ledgers["donor-fixture-b"]["currentCallerDonorCreditAtomic"] == "40000000"
                     and ledgers["foundation"]["currentCallerDonorCreditAtomic"] is None, "Donor-credit privacy mismatch")
            lv = call("ProcurementEscrowV2", "getLedger", project["businessId"])
            free = int(call("ProcurementEscrowV2", "freeLocked", project["businessId"]))
            liability = int(lv[1]) + int(lv[4]) - int(lv[3]) - int(lv[5])
            escrow_balance = int(call("MockHKD", "balanceOf", gateway.contract_address("ProcurementEscrowV2")))
            foundation_balance = int(call("MockHKD", "balanceOf", gateway.roles["foundation"]))
            _require(free == 20_000_000 and liability == escrow_balance == 100_000_000 and foundation_balance == 0, "Balance/freeLocked/liability mismatch")
            with factory() as session:
                projection = session.execute(text("SELECT deposits_atomic,reserved_atomic,released_atomic,returned_atomic FROM ledger_projections WHERE project_id=:id"),
                                             {"id": project["id"]}).one()
                _require([int(x) for x in projection] == [100_000_000, 80_000_000, 0, 0], "PostgreSQL event-derived ledger mismatch")
                rows = session.execute(text("SELECT kind,status FROM signing_requests WHERE submitted_operation_id IS NOT NULL")).all()
                _require(sorted(rows) == sorted([(k, "confirmed") for k in ("ai_pre", "reserve", "ai_pre", "reserve", "receipt")]), "Signing DB records not all confirmed")
            _require(_hex(gateway.w3.eth.get_block(checkpoint_block)["hash"]) == checkpoint_hash,
                     "Checkpoint block changed during independent verification")
            proof = {"scenario": "real-loopback-HTTP-100-80-72-ReceiptConfirmed", "syntheticEvidence": True, "realAI": False,
                     "realMoney": False, "paymentOrReleaseHTTP": False, "runId": gateway.run_id, "instanceId": gateway.instance_id, "chainId": 31337,
                     "projectId": project["id"], "projectBusinessId": project["businessId"], "procurementId": procurement["id"], "procurementBusinessId": procurement["businessId"],
                     "checkpoint": {"blockNumber": checkpoint_block, "blockHash": checkpoint_hash,
                                    "stateEnum": 6, "state": "ReceiptConfirmed", "depositsAtomic": "100000000", "reservedAtomic": "80000000", "invoiceAtomic": "72000000",
                                    "freeLockedAtomic": str(free), "releasedAtomic": "0", "projectLiabilityAtomic": str(liability), "escrowTokenBalanceAtomic": str(escrow_balance),
                                    "foundationTokenBalanceAtomic": str(foundation_balance), "donorCreditsAtomic": {"donor": "60000000", "donor-fixture-b": "40000000"},
                                    "receiptDigest": receipt_digest, "postgresProjectionMatches": True},
                     "operations": s.operations, "typedDataChecks": s.signing, "renewalChecks": renewal,
                     "acceptedReceiptOracle": receipt_oracle, "currentViewHashOracle": view_oracle,
                     "scopeLimit": "No FINAL AI, release, settlement, close, refund or three-PC booth execution."}
            # Capture checkpoint first; this 1-atomic donation is NEVER drained.
            pending = s.post(project_path + "/donations", {"amountAtomic": "1"}, "donor", "m3.2-reset-old-pending")["operation"]
            _require(pending["status"] == "queued", "Reset probe was not queued")
            proof["resetProbePrepared"] = {"pendingOperationId": pending["operationId"], "amountAtomic": "1", "broadcast": False}
            _require(int(gateway.call("ProcurementEscrowV2", "getLedger", project["businessId"])[1]) == 100_000_000, "Unsent reset probe changed deposits")
            context = ResetContext(project["id"], procurement["id"], receipt_request["id"], pending["operationId"], gateway.run_id, gateway.instance_id, signature)
            context.document_id, context.document_version_id = receipt_doc["id"], receipt_doc["versionId"]
            json.dumps(proof)
            return proof, context
    finally:
        worker.close()
        indexer.close()
        engine.dispose()


def check_reset(base_url: str, database_url: str, manifest_path: Path, api_source: Path,
                artifact_dir: Path, context: ResetContext) -> dict[str, Any]:
    """After owner resets/restarts/reseeds, check actual fresh namespace isolation.

    No reset command is issued here. Fresh-chain zero facts are explicitly not
    the earlier 100/80 balances. EIP712 itself does not bind the Anvil instance.
    """
    _loopback_url(base_url)
    _require(isinstance(context, ResetContext) and Path(artifact_dir).is_dir(), "Missing private reset context")
    _owned_context(base_url, manifest_path, artifact_dir)
    _require(context.document_id and context.document_version_id, "Missing retained synthetic document reset context")
    import httpx
    from sqlalchemy import text
    Gateway, build_engine, build_factory, Worker, Indexer, safety = _official(api_source)
    _database_guard(database_url, safety)
    gateway = Gateway(Path(manifest_path), Path(api_source))
    _require(gateway.run_id != context.old_run_id and gateway.instance_id != context.old_instance_id, "No new chain run/instance")
    engine = build_engine(database_url)
    factory = build_factory(engine)
    worker, indexer = Worker(database_url, gateway), Indexer(database_url, gateway)
    try:
        with httpx.Client(base_url=base_url, timeout=30, trust_env=False, follow_redirects=False) as client:
            tokens, _ = _login(client, gateway.manifest)
            probes = [("GET", f"/v2/projects/{context.project_id}", "foundation", None),
                      ("GET", f"/v2/projects/{context.project_id}/ledger", "donor", None),
                      ("GET", f"/v2/procurements/{context.procurement_id}", "foundation", None),
                      ("GET", f"/v2/documents/{context.document_id}", "recipient", None),
                      ("GET", f"/v2/documents/{context.document_id}/content", "recipient", None),
                      ("GET", f"/v2/operations/{context.pending_operation_id}", "donor", None),
                      ("GET", f"/v2/signing-requests/{context.receipt_request_id}", "recipient", None),
                      ("POST", f"/v2/signing-requests/{context.receipt_request_id}/submit", "recipient", {"signature": context.receipt_signature})]
            rejected = []
            for method, path, username, body in probes:
                response = client.request(method, path, json=body,
                                          headers=_auth(tokens[username], "m3.2-reset-reject-old-signature" if method == "POST" else None))
                _require(response.status_code == 404, "New namespace exposed or accepted an old resource")
                rejected.append({"method": method, "resource": path, "httpStatus": 404})
            for username in ("foundation", "recipient", "donor", "admin"):
                items = _response(client.get("/v2/projects", headers=_auth(tokens[username])), 200,
                                  "fresh project list")["items"]
                _require(items == [], "Fresh namespace HTTP list adopted a retained old project")
            with factory() as session:
                status, error = session.execute(text("SELECT status,error_code FROM operations WHERE id=:id"), {"id": context.pending_operation_id}).one()
                _require((status, error) == ("invalidated_instance", "deployment_instance_changed"), "Old pending operation not invalidated")
                _require(session.execute(text("SELECT count(*) FROM chain_transactions WHERE operation_id=:id"),
                                         {"id": context.pending_operation_id}).scalar_one() == 0, "Unsent reset probe has transaction history")
                old_namespace, old_active = session.execute(text(
                    "SELECT id,active FROM deployment_instances WHERE run_id=:run AND instance_id=:instance"),
                    {"run": context.old_run_id, "instance": context.old_instance_id}).one()
                new_namespace, new_active = session.execute(text(
                    "SELECT id,active FROM deployment_instances WHERE run_id=:run AND instance_id=:instance"),
                    {"run": gateway.run_id, "instance": gateway.instance_id}).one()
                _require(old_active is False and new_active is True and old_namespace != new_namespace,
                         "Old/new namespace activity identity is not isolated")
                old_ledger = session.execute(text(
                    "SELECT deposits_atomic,reserved_atomic,released_atomic,returned_atomic "
                    "FROM ledger_projections WHERE namespace_id=:ns AND project_id=:id"),
                    {"ns": old_namespace, "id": context.project_id}).one()
                _require([int(x) for x in old_ledger] == [100_000_000, 80_000_000, 0, 0],
                         "Reset erased or altered retained historical ledger evidence")
                old_credit_count, old_credit_total = session.execute(text(
                    "SELECT count(*),coalesce(sum(credit_atomic),0) FROM donor_credit_projections "
                    "WHERE namespace_id=:ns AND project_id=:id"),
                    {"ns": old_namespace, "id": context.project_id}).one()
                _require(int(old_credit_count) == 2 and int(old_credit_total) == 100_000_000,
                         "Reset erased or altered retained original donor credits")
                old_proc = session.execute(text(
                    "SELECT chain_status,reserved_amount_atomic,invoice_amount_atomic FROM procurements "
                    "WHERE namespace_id=:ns AND id=:id"),
                    {"ns": old_namespace, "id": context.procurement_id}).one()
                _require(old_proc[0] == "receipt_confirmed" and int(old_proc[1]) == 80_000_000
                         and int(old_proc[2]) == 72_000_000, "Retained procurement checkpoint was erased or adopted")
                retained_document = session.execute(text(
                    "SELECT count(*) FROM documents d JOIN document_versions v ON v.document_id=d.id "
                    "WHERE d.namespace_id=:ns AND d.id=:document AND v.id=:version"),
                    {"ns": old_namespace, "document": context.document_id, "version": context.document_version_id}).scalar_one()
                _require(retained_document == 1, "Historical synthetic document/version was erased")
                fresh_counts = {}
                for table in ("projects", "procurements", "ledger_projections", "donor_credit_projections", "documents", "signing_requests"):
                    fresh_counts[table] = int(session.execute(text(
                        f"SELECT count(*) FROM {table} WHERE namespace_id=:ns"), {"ns": new_namespace}).scalar_one())
                _require(all(value == 0 for value in fresh_counts.values()), "New namespace adopted old business/read-model records")
                namespace_proof = {"oldNamespaceId": str(old_namespace), "newNamespaceId": str(new_namespace),
                                   "oldActive": False, "newActive": True,
                                   "retainedOldLedgerAtomic": [str(int(x)) for x in old_ledger],
                                   "retainedOldDonorCreditCount": 2, "retainedOldDonorCreditTotalAtomic": "100000000",
                                   "retainedOldProcurementState": "receipt_confirmed", "retainedOldSyntheticDocumentVersions": 1,
                                   "newNamespaceBusinessRecordCounts": fresh_counts, "freshHttpProjectListsEmpty": True}
            before = {r: int(gateway.w3.eth.get_transaction_count(a)) for r, a in gateway.roles.items()}
            tip = int(gateway.w3.eth.block_number)
            worker.once()
            indexer.once()
            with factory() as session:
                for table in fresh_counts:
                    count = session.execute(text(f"SELECT count(*) FROM {table} WHERE namespace_id=:ns"),
                                            {"ns": new_namespace}).scalar_one()
                    _require(count == 0, "New indexer/worker adopted a retained old read-model record")
            namespace_proof["freshRecordCountsRemainZeroAfterWorkerIndexer"] = True
            after = {r: int(gateway.w3.eth.get_transaction_count(a)) for r, a in gateway.roles.items()}
            _require(before == after and int(gateway.w3.eth.block_number) == tip, "New instance broadcast old work")
            _require(gateway.call("PoGRegistryV2", "projectCount") == 0 and gateway.call("MockHKD", "balanceOf", gateway.contract_address("ProcurementEscrowV2")) == 0,
                     "Fresh-chain project/escrow zero facts mismatch")
            nonces = {"ai": int(gateway.call("PoGRegistryV2", "aiNonces", gateway.roles["aiSigner"])),
                      "recipient": int(gateway.call("PoGRegistryV2", "recipientNonces", gateway.roles["recipient"])),
                      "human": int(gateway.call("ProcurementEscrowV2", "humanNonces", gateway.roles["humanApprover"]))}
            _require(nonces == {"ai": 0, "recipient": 0, "human": 0}, "Fresh signature nonces are not zero")
            proof = {"scenario": "actual-owned-node-reset-instance-isolation", "chainId": 31337, "oldRunId": context.old_run_id,
                     "oldInstanceId": context.old_instance_id, "newRunId": gateway.run_id, "newInstanceId": gateway.instance_id,
                     "oldPendingStatus": status, "oldPendingErrorCode": error, "oldPendingBroadcast": False, "rejectedOldResourceRequests": rejected,
                     "namespaceReadModelIsolation": namespace_proof,
                     "freshChain": {"projectCount": 0, "escrowTokenBalanceAtomic": "0", "signingNonces": nonces},
                     "scopeLimit": "Namespace isolation, not EIP712 instance binding; discard old signatures/business IDs."}
            json.dumps(proof)
            return proof
    finally:
        worker.close()
        indexer.close()
        engine.dispose()
