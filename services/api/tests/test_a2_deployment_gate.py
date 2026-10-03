from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading

from eth_utils import keccak
import pytest
from web3.exceptions import TransactionNotFound

from pog_api.chain import (
    ChainMismatch, ChainNotBroadcast, ChainUnavailable, LocalChainGateway, PreparedEnvelope,
    _LoopbackSession, _loopback_port,
)


@pytest.fixture(autouse=True)
def clean_database():
    """These deployment gate tests are pure read-only tests without DB fixtures."""
    yield


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:18545", "http://example.com:18545", "http://0.0.0.0:18545",
    "http://127.0.0.1:18545/path", "http://127.0.0.1:18545?next=http://example.com",
    "http://127.0.0.1:18545#fragment", "http://user@127.0.0.1:18545",
    "http://user:password@127.0.0.1:18545", "http://localhost:18545",
    "http://127.0.0.1", "http://127.0.0.1:80", "http://127.0.0.1:99999",
    "http://127.0.0.1:18545/", "http://127.0.0.1:18545;ignored",
])
def test_rpc_url_rejects_credentials_escapes_and_unmanaged_variants(url):
    with pytest.raises(ChainMismatch):
        _loopback_port(url)


def test_rpc_url_accepts_only_exact_managed_literal_loopback():
    assert _loopback_port("http://127.0.0.1:18545") == 18545


def test_rpc_session_never_contacts_a_redirect_target():
    contacted = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            contacted.append(self.path)
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/escaped")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        session = _LoopbackSession()
        session.trust_env = False
        with pytest.raises(ChainMismatch, match="redirects are forbidden"):
            session.post(f"http://127.0.0.1:{server.server_port}/rpc", json={}, allow_redirects=True)
        assert contacted == ["/rpc"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _Function:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value

    def _encode_transaction_data(self):
        return self.value


@pytest.fixture
def anchored_gateway(tmp_path):
    """Model fresh RPC facts after a successful, separately tested startup gate."""
    gateway = object.__new__(LocalChainGateway)
    roles = {name: "0x" + f"{index + 1:040x}" for index, name in enumerate(gateway.ROLE_NAMES)}
    addresses = {name: "0x" + f"{index + 100:040x}" for index, name in enumerate(gateway.ABI_PATHS)}
    facts = {
        "chainId": 31337, "genesis": "0x" + "11" * 32, "instance": "node-a",
        "blockHash": "0x" + "22" * 32, "receiptStatus": 1,
        "accounts": list(roles.values()), "owner": roles["deployerOwner"],
        "domainVersion": "2", "domainName": None,
        "bootstrapBlockHash": "0x" + "33" * 32,
        "bootstrapStatus": 1, "bootstrapCaller": roles["deployerOwner"],
        "bootstrapDestination": None, "bootstrapData": None,
    }
    records = {
        name: {"address": address, "runtime": {"keccak256": "0x" + keccak(b"accepted").hex()},
               "deployment": {"transactionHash": "0x" + f"{index + 1:064x}",
                              "blockHash": facts["blockHash"], "blockNumber": index + 1}}
        for index, (name, address) in enumerate(addresses.items())
    }
    bootstrap = {
        name: {"transactionHash": "0x" + f"{index + 100:064x}",
               "blockHash": facts["bootstrapBlockHash"], "blockNumber": index + 4}
        for index, name in enumerate(("bindEscrow", "allowAISigner", "mintDonorA", "mintDonorB"))
    }
    gateway.manifest = {
        "runId": "run-a", "chain": {"instanceId": "node-a", "genesisHash": facts["genesis"]},
        "roles": roles, "contracts": records,
        "bootstrap": {"transactions": bootstrap},
    }
    gateway.manifest_path = tmp_path / "manifest.json"
    gateway.manifest_path.write_text(json.dumps(gateway.manifest), encoding="utf-8")
    gateway._manifest_digest = hashlib.sha256(gateway.manifest_path.read_bytes()).hexdigest()
    gateway._manifest_object_digest = hashlib.sha256(json.dumps(
        gateway.manifest, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    gateway._verified_fingerprint = "accepted-fingerprint"
    gateway._artifact_fingerprint = lambda: "accepted-fingerprint"
    contracts = {}

    class Eth:
        @property
        def chain_id(self):
            return facts["chainId"]

        @property
        def accounts(self):
            return facts["accounts"]

        def get_block(self, number):
            return {"number": number, "hash": facts["genesis"] if number == 0 else (
                facts["bootstrapBlockHash"] if number >= 4 else facts["blockHash"]
            )}

        def get_code(self, _address):
            return b"accepted"

        def get_transaction_receipt(self, transaction_hash):
            for record in bootstrap.values():
                if record["transactionHash"] == transaction_hash:
                    return {"status": facts["bootstrapStatus"], "blockHash": facts["bootstrapBlockHash"],
                            "blockNumber": record["blockNumber"], "contractAddress": None}
            record = next(value for value in records.values()
                          if value["deployment"]["transactionHash"] == transaction_hash)
            return {"status": facts["receiptStatus"], "blockHash": facts["blockHash"],
                    "blockNumber": record["deployment"]["blockNumber"],
                    "contractAddress": record["address"]}

        def get_transaction(self, transaction_hash):
            name = next(name for name, record in bootstrap.items()
                        if record["transactionHash"] == transaction_hash)
            destination = addresses["PoGRegistryV2" if name in {"bindEscrow", "allowAISigner"} else "MockHKD"]
            return {"from": facts["bootstrapCaller"], "to": facts["bootstrapDestination"] or destination,
                    "input": facts["bootstrapData"] or name}

    for name, address in addresses.items():
        functions = SimpleNamespace(
            eip712Domain=lambda name=name, address=address: _Function([
                b"\x0f", facts["domainName"] or name, facts["domainVersion"], 31337, address,
            ]),
            owner=lambda: _Function(facts["owner"]),
            escrow=lambda: _Function(addresses["ProcurementEscrowV2"]),
            registry=lambda: _Function(addresses["PoGRegistryV2"]),
            decimals=lambda: _Function(6), name=lambda: _Function("Mock Hong Kong Dollar"),
            symbol=lambda: _Function("mHKD"), aiSigners=lambda _signer: _Function(True),
            bindEscrow=lambda _escrow: _Function("bindEscrow"),
            setAISigner=lambda _signer, _allowed: _Function("allowAISigner"),
            mint=lambda donor, _amount: _Function("mintDonorA" if donor.lower() == roles["donorA"].lower() else "mintDonorB"),
        )
        contracts[name] = SimpleNamespace(address=address, functions=functions)
    gateway.contracts = contracts
    gateway.w3 = SimpleNamespace(
        eth=Eth(), is_connected=lambda: True,
        provider=SimpleNamespace(make_request=lambda *_args: {"result": {"instanceId": facts["instance"]}}),
    )
    return gateway, facts


def test_cached_startup_still_checks_fresh_canonical_authorities(anchored_gateway):
    gateway, _facts = anchored_gateway
    gateway.verify()


@pytest.mark.parametrize("field,value,error", [
    ("chainId", 1, "chainId"), ("genesis", "0x" + "aa" * 32, "genesis"),
    ("instance", "node-b", "instanceId"), ("owner", "0x" + "bb" * 20, "owner"),
    ("domainVersion", "1", "domain"), ("domainName", "PoGRegistry", "domain"),
    ("blockHash", "0x" + "cc" * 32, "receipt"), ("receiptStatus", 0, "receipt"),
])
def test_cached_gate_rejects_changed_chain_receipt_owner_and_domain(
    anchored_gateway, field, value, error,
):
    gateway, facts = anchored_gateway
    facts[field] = value
    with pytest.raises(ChainMismatch, match=error):
        gateway.verify()


def test_cached_gate_rejects_role_account_reordering(anchored_gateway):
    gateway, facts = anchored_gateway
    facts["accounts"] = list(reversed(facts["accounts"]))
    with pytest.raises(ChainMismatch, match="wallets"):
        gateway.verify()


@pytest.mark.parametrize("field,value", [
    ("bootstrapBlockHash", "0x" + "dd" * 32), ("bootstrapStatus", 0),
    ("bootstrapCaller", "0x" + "ee" * 20),
    ("bootstrapDestination", "0x" + "ff" * 20), ("bootstrapData", "0x1234"),
])
def test_cached_gate_rejects_reverted_or_replaced_bootstrap(anchored_gateway, field, value):
    gateway, facts = anchored_gateway
    facts[field] = value
    with pytest.raises(ChainMismatch, match="bootstrap"):
        gateway.verify()


def test_cached_gate_rejects_local_artifact_and_manifest_change(anchored_gateway):
    gateway, _facts = anchored_gateway
    gateway._artifact_fingerprint = lambda: "tampered-fingerprint"
    with pytest.raises(ChainMismatch, match="inputs changed"):
        gateway.verify()
    gateway._artifact_fingerprint = lambda: "accepted-fingerprint"
    gateway.manifest["roles"]["foundation"] = gateway.manifest["roles"]["recipient"]
    with pytest.raises(ChainMismatch, match="inputs changed"):
        gateway.verify()


def test_startup_anchor_invokes_accepted_verify_and_cache_never_skips_fresh_check(
    tmp_path, monkeypatch,
):
    gateway = object.__new__(LocalChainGateway)
    gateway.repository_root = tmp_path / "repo"
    gateway.manifest_path = tmp_path / "pog-local-gate" / "manifest.json"
    gateway.manifest = {"runId": "run-a", "chain": {
        "instanceId": "node-a", "rpcUrl": "http://127.0.0.1:18545",
    }}
    gateway._rpc_port = 18545
    report = {
        "status": "ready", "runId": "run-a", "instanceId": "node-a",
        "rpcUrl": "http://127.0.0.1:18545", "chainId": 31337,
        "manifestPath": str(gateway.manifest_path),
    }
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr("pog_api.chain.subprocess.run", run)
    gateway._run_accepted_verifier()
    args, kwargs = calls[0]
    assert args[1:] == [str(gateway.repository_root / "scripts/local-chain.py"), "verify",
                        "--port", "18545", "--state-dir", str(gateway.manifest_path.parent)]
    assert kwargs["timeout"] == 45
    assert not any("proxy" in key.lower() for key in kwargs["env"])

    fingerprint = "unique-unit-gate-fingerprint"
    gateway._artifact_fingerprint = lambda: fingerprint
    LocalChainGateway._verified_fingerprints.pop(fingerprint, None)
    gateway._startup_gate()
    gateway._startup_gate()
    assert len(calls) == 2  # Explicit invocation plus one uncached startup.
    LocalChainGateway._verified_fingerprints.pop(fingerprint, None)


def test_startup_anchor_rejects_failure_and_mismatched_report(tmp_path, monkeypatch):
    gateway = object.__new__(LocalChainGateway)
    gateway.repository_root = tmp_path
    gateway.manifest_path = tmp_path / "manifest.json"
    gateway._rpc_port = 18545
    gateway.manifest = {"runId": "run-a", "chain": {
        "instanceId": "node-a", "rpcUrl": "http://127.0.0.1:18545",
    }}
    for result in (
        SimpleNamespace(returncode=1, stdout="", stderr="artifact/domain/receipt mismatch"),
        SimpleNamespace(returncode=0, stdout=json.dumps({"status": "ready"}), stderr=""),
    ):
        monkeypatch.setattr("pog_api.chain.subprocess.run", lambda *_args, result=result, **_kwargs: result)
        with pytest.raises(ChainMismatch):
            gateway._run_accepted_verifier()


def test_send_distinguishes_proven_not_broadcast_from_response_loss():
    gateway = object.__new__(LocalChainGateway)
    envelope = PreparedEnvelope(
        caller="0x" + "11" * 20, to="0x" + "22" * 20, chain_id=31337,
        nonce=7, data="0xabcd", value=0, action="donation.deposit", expected_event="Donated",
    )
    sent = []

    def rejected(_transaction):
        raise ValueError("execution reverted: known preflight error")

    def response_lost(transaction):
        sent.append(transaction)
        raise TimeoutError("accepted RPC response lost")

    gateway.verify = lambda: None
    gateway.w3 = SimpleNamespace(eth=SimpleNamespace(
        estimate_gas=rejected, send_transaction=response_lost,
    ))
    with pytest.raises(ChainNotBroadcast, match="known preflight error"):
        gateway.send(envelope)
    assert sent == []
    gateway.w3.eth.estimate_gas = lambda _transaction: 123456
    with pytest.raises(TimeoutError, match="response lost"):
        gateway.send(envelope)
    assert sent[0]["nonce"] == 7 and sent[0]["gas"] == 123456


@pytest.mark.parametrize("method", ["receipt", "receipt_with_events"])
def test_receipt_transport_outage_is_not_a_missing_or_failed_transaction(method):
    gateway = object.__new__(LocalChainGateway)

    def unavailable(_transaction):
        raise TimeoutError("read timed out after submission")

    gateway.w3 = SimpleNamespace(eth=SimpleNamespace(get_transaction_receipt=unavailable))
    arguments = ("0x" + "11" * 32,) if method == "receipt" else ("0x" + "11" * 32, "Donated")
    with pytest.raises(ChainUnavailable, match="temporarily unavailable"):
        getattr(gateway, method)(*arguments)

    def absent(_transaction):
        raise TransactionNotFound("not mined")

    gateway.w3.eth.get_transaction_receipt = absent
    assert getattr(gateway, method)(*arguments) == (None if method == "receipt" else (None, []))


def test_manifest_receipt_absence_is_gate_mismatch_and_transport_is_unavailable(anchored_gateway):
    gateway, _facts = anchored_gateway

    def absent(_transaction):
        raise TransactionNotFound("canonical bootstrap was removed")

    gateway.w3.eth.get_transaction_receipt = absent
    with pytest.raises(ChainMismatch, match="transaction is missing"):
        gateway.verify()

    def unavailable(_transaction):
        raise TimeoutError("temporary receipt lookup outage")

    gateway.w3.eth.get_transaction_receipt = unavailable
    with pytest.raises(ChainUnavailable) as error:
        gateway.verify()
    assert not isinstance(error.value, ChainMismatch)


@pytest.mark.parametrize("method", ["block_identity", "latest_timestamp", "events_in_range", "call"])
def test_read_rpc_transport_failures_use_the_dependency_boundary(method):
    gateway = object.__new__(LocalChainGateway)
    gateway.verify = lambda: None

    def unavailable(*_args, **_kwargs):
        raise TimeoutError("temporary local RPC outage")

    event = SimpleNamespace(get_logs=unavailable)
    function = SimpleNamespace(call=unavailable)
    gateway.contracts = {"PoGRegistryV2": SimpleNamespace(
        abi=[{"type": "event", "name": "Donated"}],
        events=SimpleNamespace(Donated=lambda: event),
        functions=SimpleNamespace(getProject=lambda *_args: function),
    )}
    gateway.w3 = SimpleNamespace(eth=SimpleNamespace(get_block=unavailable))
    arguments = {
        "block_identity": (1,), "latest_timestamp": (), "events_in_range": (1, 1),
        "call": ("PoGRegistryV2", "getProject", "0x" + "11" * 32),
    }
    with pytest.raises(ChainUnavailable, match="unavailable"):
        getattr(gateway, method)(*arguments[method])
