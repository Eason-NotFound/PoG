#!/usr/bin/env python3
"""PoG M3.1: owned, disposable, loopback-only Anvil deployment (no real money).

No private keys or seed phrases are read, generated, saved or printed here.
Anvil's ten public, unlocked development accounts are NOT secure identities.
Stopping retains records, not a resumable blockchain; use an explicit reset.
"""

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = ROOT / "contracts/deployments/local"
CHAIN_ID = 31337
FOUNDRY_VERSION = "1.8.4"
FOUNDRY_COMMIT = "50af4efe189dc64bad2b75ed6990b835de66c4ae"
ACCEPTED_TAG = "blockchain-v0.2.0-m2"
ACCEPTED_MERGE = "61aa673653dd31188d2627d76cbba3f97fed6137"
SNAPSHOT = "810a54ea9d17c5ac80f1974035f690e49941e1e1"
DONOR_MINT = 1_000_000_000
ROLE_NAMES = (
    "deployerOwner", "foundation", "recipient", "aiSigner", "humanApprover",
    "donorA", "donorB", "vendor", "relayer", "mockRedemption",
)
CONTRACT_NAMES = ("MockHKD", "PoGRegistryV2", "ProcurementEscrowV2")


class LocalError(Exception):
    """A fail-closed, safe-to-display local deployment error."""


class NodeUnavailable(LocalError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, handle, code, message, headers, new_url):
        raise LocalError("Loopback RPC redirects are forbidden; no external endpoint is contacted.")


def require(condition, message):
    if not condition:
        raise LocalError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def clean_env():
    # Ignore ETH_*, ANVIL_*, FOUNDRY_* and proxy settings inherited from shells.
    return {key: value for key, value in os.environ.items()
            if key in ("PATH", "HOME", "USER", "TMPDIR", "SYSTEMROOT")}


def tool(name):
    candidate = shutil.which(name) or str(Path.home() / ".foundry/bin" / name)
    require(Path(candidate).is_file(), f"Missing {name}; install pinned Foundry {FOUNDRY_VERSION}.")
    result = subprocess.run([candidate, "--version"], capture_output=True, text=True,
                            env=clean_env(), timeout=10)
    require(result.returncode == 0 and
            f"Version: {FOUNDRY_VERSION}" in result.stdout and
            f"Commit SHA: {FOUNDRY_COMMIT}" in result.stdout,
            f"{name} must be pinned Foundry {FOUNDRY_VERSION} ({FOUNDRY_COMMIT}).")
    return str(Path(candidate).resolve())


def command(args, timeout=60):
    result = subprocess.run(args, cwd=ROOT, env=clean_env(), capture_output=True,
                            text=True, timeout=timeout)
    require(result.returncode == 0,
            f"Local {Path(args[0]).name} command failed; inspect local tooling/configuration.")
    return result.stdout.strip()


def safe_state_dir(raw):
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    require(not path.is_symlink(), "State directory cannot be a symlink.")
    resolved = path.resolve()
    temp_root = Path("/tmp").resolve()
    in_local = resolved == LOCAL_ROOT or LOCAL_ROOT in resolved.parents
    in_temp = temp_root in resolved.parents and any(
        part.startswith("pog-local-") for part in resolved.relative_to(temp_root).parts
    )
    require(in_local or in_temp,
            "State directory must be contracts/deployments/local[/...] or /tmp/pog-local-*[/...].")
    return resolved


def safe_file(path):
    require(not path.is_symlink(), f"Refusing symlink: {path.name}.")
    require(not path.exists() or path.is_file(), f"Not a regular file: {path.name}.")


def read_json(path):
    safe_file(path)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise LocalError(f"Cannot read valid {path.name}; preserve files and investigate.") from exc


def write_json(path, value):
    safe_file(path)
    temporary = path.with_name(path.name + ".new")
    safe_file(temporary)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


@contextlib.contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(directory.stat().st_uid == os.getuid(), "State directory belongs to another user.")
    os.chmod(directory, 0o700)
    lock_path = directory / ".lock"
    safe_file(lock_path)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalError("Another local-chain command holds this state directory's lock.") from exc
        yield
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def port_locked(port):
    # Shared across state directories/repository copies belonging to this user.
    # Locks always acquire state directory first, then port, without waiting.
    directory = Path("/tmp").resolve() / f"pog-local-locks-{os.getuid()}"
    require(not directory.is_symlink(), "Port lock directory cannot be a symlink.")
    directory.mkdir(exist_ok=True, mode=0o700)
    require(directory.stat().st_uid == os.getuid(), "Port lock directory belongs to another user.")
    os.chmod(directory, 0o700)
    path = directory / f"port-{port}.lock"
    safe_file(path)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LocalError("Another local-chain command holds this local port's lock.") from exc
        yield
    finally:
        os.close(descriptor)


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


class Rpc:
    def __init__(self, port):
        self.url = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, method, params=None):
        request = urllib.request.Request(
            self.url,
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                        "params": [] if params is None else params}).encode(),
            {"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=3) as response:
                result = json.load(response)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise NodeUnavailable("Loopback RPC unavailable or invalid.") from exc
        require("error" not in result and "result" in result,
                f"Local RPC rejected {method}; no fallback to an external endpoint.")
        return result["result"]


def fingerprint(pid):
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "lstart=", "-o", "command="],
                            capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ""


def process_matches(state):
    pid = state.get("pid")
    expected = state.get("processFingerprint")
    expected_command = " ".join(anvil_arguments(tool("anvil"), state.get("port")))
    return (isinstance(pid, int) and pid > 1 and isinstance(expected, str) and
            expected.endswith(" " + expected_command) and fingerprint(pid) == expected)


def anvil_arguments(anvil, port):
    return [anvil, "--host", "127.0.0.1", "--port", str(port),
            "--chain-id", str(CHAIN_ID), "--hardfork", "cancun", "--accounts", "10",
            "--balance", "10000", "--quiet", "--no-cors"]


def state_shape(state, port):
    require(state.get("schemaVersion") == 1 and state.get("port") == port,
            "State schema/port mismatch; do not adopt a different node.")
    require(re.fullmatch(r"[0-9a-f]{32}", state.get("runId", "")) is not None,
            "Invalid local run ID; preserve files and investigate.")
    require(state.get("rpcUrl") == f"http://127.0.0.1:{port}" and
            state.get("chainId") == CHAIN_ID, "State is not this local chain.")


def owned_node(state, rpc):
    require(process_matches(state), "Anvil PID/start identity mismatch; will not signal/adopt this process.")
    require(int(rpc.call("eth_chainId"), 16) == CHAIN_ID, "Unexpected chain ID.")
    metadata = rpc.call("anvil_metadata")
    require(metadata.get("instanceId") == state.get("instanceId"),
            "Stale Anvil instance ID; same addresses do not identify the same chain.")
    require(rpc.call("eth_getBlockByNumber", ["0x0", False])["hash"] == state.get("genesisHash"),
            "Stale genesis hash; do not use this manifest.")


def baselines():
    for baseline in ("blockchain-m1.sha256", "blockchain-m2-v2-rc.1.sha256"):
        for line in (ROOT / "docs/baselines" / baseline).read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            expected, relative = line.split(maxsplit=1)
            relative = relative.lstrip("*")
            require(sha256((ROOT / relative).read_bytes()) == expected,
                    f"Accepted technical baseline changed: {relative}.")
    accepted = command(["git", "rev-parse", f"{ACCEPTED_TAG}^{{commit}}"])
    require(accepted == ACCEPTED_MERGE, "Accepted M2 tag does not match the frozen merge.")


def artifacts():
    package = read_json(ROOT / "packages/contract-abis/v2/package.json")
    result = {}
    for name in CONTRACT_NAMES:
        artifact_path = ROOT / f"contracts/out/{name}.sol/{name}.json"
        artifact = read_json(artifact_path)
        abi_path = ROOT / f"packages/contract-abis/v2/{name}.json"
        require(artifact["abi"] == read_json(abi_path), f"Generated {name} ABI mismatch.")
        require(artifact["metadata"]["compiler"]["version"] == "0.8.24+commit.e11b9ed9",
                "Deployment compiler differs from accepted Solc 0.8.24.")
        result[name] = {"artifact": artifact, "path": str(artifact_path.relative_to(ROOT)),
                        "abiPath": str(abi_path.relative_to(ROOT)),
                        "abiSha256": sha256(abi_path.read_bytes()),
                        "artifactId": f"{package['name']}@{package['version']}"}
    return result


def calldata(cast, signature, *arguments):
    return command([cast, "calldata", signature, *map(str, arguments)])


def call_value(rpc, cast, address, signature, *arguments):
    return rpc.call("eth_call", [{"to": address, "data": calldata(cast, signature, *arguments)}, "latest"])


def address_value(value):
    require(isinstance(value, str) and len(value) == 66, "Invalid address return value.")
    return "0x" + value[-40:]


def transaction_receipt(rpc, transaction_hash):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        receipt = rpc.call("eth_getTransactionReceipt", [transaction_hash])
        if receipt is not None:
            require(int(receipt["status"], 16) == 1, "Local deployment/bootstrap transaction reverted.")
            return {"transactionHash": transaction_hash, "blockHash": receipt["blockHash"],
                    "blockNumber": int(receipt["blockNumber"], 16),
                    "contractAddress": receipt.get("contractAddress")}
        time.sleep(0.1)
    raise LocalError("Timed out waiting for local transaction; preserve partial run and reset explicitly.")


def send(rpc, cast, sender, address, signature, *arguments):
    transaction_hash = rpc.call("eth_sendTransaction", [{
        "from": sender, "to": address, "data": calldata(cast, signature, *arguments),
        "gas": hex(10_000_000),
    }])
    return transaction_receipt(rpc, transaction_hash)


def deploy(rpc, forge, name, sender, constructor_arguments):
    args = [forge, "create", f"contracts/src/{name}.sol:{name}", "--broadcast", "--unlocked",
            "--from", sender, "--rpc-url", rpc.url, "--no-proxy", "--json", "--timeout", "20"]
    if constructor_arguments:
        args += ["--constructor-args", *constructor_arguments]
    output = json.loads(command(args))
    address = output.get("deployedTo")
    require(isinstance(address, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", address) is not None,
            "Forge did not report a deployment address.")
    receipt = transaction_receipt(rpc, output["transactionHash"])
    require(receipt["contractAddress"].lower() == address.lower(), "Deployment receipt address mismatch.")
    return address, receipt


def strip_immutables(runtime, references):
    masked = bytearray(runtime)
    for entries in references.values():
        for entry in entries:
            start, length = entry["start"], entry["length"]
            require(0 <= start and 0 < length and start + length <= len(masked),
                    "Artifact immutable reference outside bytecode.")
            masked[start:start + length] = bytes(length)
    return bytes(masked)


def canonical_references(references):
    # AST IDs differ when Forge compiles a target rather than the full test tree.
    # Byte offsets/lengths are stable; never use those incidental IDs as identity.
    groups = [sorted(entries, key=lambda entry: (entry["start"], entry["length"]))
              for entries in references.values()]
    groups.sort(key=lambda entries: tuple((entry["start"], entry["length"]) for entry in entries))
    return {f"immutableGroup{index}": entries for index, entries in enumerate(groups)}


def creation_input(cast, artifact, arguments):
    bytecode = artifact["bytecode"]["object"]
    if arguments:
        encoded = command([cast, "abi-encode", "constructor(address)", *arguments])
        return bytecode + encoded[2:]
    return bytecode


def verify_receipt(rpc, recorded):
    actual = rpc.call("eth_getTransactionReceipt", [recorded["transactionHash"]])
    require(actual is not None and int(actual["status"], 16) == 1,
            "Missing or reverted manifest transaction.")
    require(actual["blockHash"] == recorded["blockHash"] and
            int(actual["blockNumber"], 16) == recorded["blockNumber"], "Stale transaction/block record.")
    canonical = rpc.call("eth_getBlockByNumber", [hex(recorded["blockNumber"]), False])
    require(canonical is not None and canonical["hash"] == recorded["blockHash"],
            "Manifest transaction is not in the canonical local chain.")
    return actual


def runtime_record(rpc, cast, information, address, receipt, sender, constructor_arguments):
    artifact = information["artifact"]
    creation = creation_input(cast, artifact, constructor_arguments)
    transaction = rpc.call("eth_getTransactionByHash", [receipt["transactionHash"]])
    require(transaction is not None and transaction["from"].lower() == sender.lower() and
            transaction["to"] is None and transaction["input"].lower() == creation.lower(),
            "Deployment transaction does not match accepted creation bytecode/constructor.")
    predicted = command([cast, "compute-address", sender, "--nonce", str(int(transaction["nonce"], 16))])
    computed_addresses = re.findall(r"0x[0-9a-fA-F]{40}", predicted)
    require(len(computed_addresses) == 1 and computed_addresses[0].lower() == address.lower(),
            "Deployment address is not the sender/nonce CREATE address.")
    runtime_hex = rpc.call("eth_getCode", [address, "latest"])
    runtime = bytes.fromhex(runtime_hex[2:])
    template = bytes.fromhex(artifact["deployedBytecode"]["object"].removeprefix("0x"))
    references = artifact["deployedBytecode"].get("immutableReferences", {})
    require(0 < len(runtime) <= 24_576 and len(runtime) == len(template), "Runtime size exceeds EIP170 or differs.")
    require(strip_immutables(runtime, references) == strip_immutables(template, references),
            "Deployed runtime differs from accepted template outside immutable slots.")
    # Execute CREATE read-only at the PRE-deployment block, with the sender's
    # original nonce. This reconstructs ALL immutable slots, including EIP712
    # cached domain/name/version/chain/address fields; mask-only is insufficient.
    reconstructed = rpc.call("eth_call", [{"from": sender, "data": creation,
                                           "gas": hex(30_000_000)},
                                          hex(receipt["blockNumber"] - 1)])
    require(reconstructed.lower() == runtime_hex.lower(),
            "Constructor-reconstructed runtime mismatch (including immutable signing fields).")
    return {
        "keccak256": command([cast, "keccak", runtime_hex]), "sha256": sha256(runtime),
        "sizeBytes": len(runtime), "immutableReferences": canonical_references(references),
        "templateMaskedSha256": sha256(strip_immutables(template, references)),
        "verification": "accepted-template-mask AND historical-constructor-full-runtime",
    }


def eip712_domain(rpc, cast, address):
    raw = bytes.fromhex(call_value(rpc, cast, address, "eip712Domain()")[2:])
    require(len(raw) >= 224, "Invalid EIP712 domain ABI result.")

    def word(index):
        return raw[index * 32:(index + 1) * 32]

    def string(index):
        offset = int.from_bytes(word(index), "big")
        require(offset + 32 <= len(raw), "Invalid domain string offset.")
        length = int.from_bytes(raw[offset:offset + 32], "big")
        require(offset + 32 + length <= len(raw), "Invalid domain string length.")
        return raw[offset + 32:offset + 32 + length].decode()

    return {"fields": word(0)[0], "name": string(1), "version": string(2),
            "chainId": int.from_bytes(word(3), "big"),
            "verifyingContract": "0x" + word(4)[-20:].hex()}


def string_value(value):
    raw = bytes.fromhex(value.removeprefix("0x"))
    require(len(raw) >= 64 and int.from_bytes(raw[:32], "big") == 32, "Invalid ABI string result.")
    length = int.from_bytes(raw[32:64], "big")
    require(64 + length <= len(raw), "Invalid ABI string length.")
    return raw[64:64 + length].decode()


def verify(state, manifest, rpc, cast):
    owned_node(state, rpc)
    require(state.get("phase") == "ready", "Partial deployment; do not resume mint/deploy. Reset explicitly.")
    require(manifest.get("schemaVersion") == 1 and manifest.get("runId") == state["runId"],
            "Manifest run ID mismatch.")
    require(manifest.get("chain") == {"chainId": CHAIN_ID, "rpcUrl": rpc.url,
                                      "genesisHash": state["genesisHash"],
                                      "instanceId": state["instanceId"]}, "Manifest chain identity mismatch.")
    require(manifest.get("version") == {"acceptedTag": ACCEPTED_TAG, "acceptedMerge": ACCEPTED_MERGE,
                                        "technicalSnapshot": SNAPSHOT}, "Manifest accepted-version mismatch.")
    require(manifest.get("foundry") == {"version": FOUNDRY_VERSION, "commit": FOUNDRY_COMMIT,
                                       "hardfork": "cancun", "eip170LimitBytes": 24_576},
            "Manifest pinned-tooling/hardfork/size-limit declaration mismatch.")
    baselines()
    information = artifacts()
    accounts = rpc.call("eth_accounts")
    roles = manifest.get("roles", {})
    require(set(roles) == set(ROLE_NAMES) and len(accounts) == len(ROLE_NAMES), "Unexpected role/accounts list.")
    require(all(roles[name].lower() == accounts[index].lower()
                for index, name in enumerate(ROLE_NAMES)), "Manifest role mapping mismatch.")
    require(len({address.lower() for address in roles.values()}) == len(ROLE_NAMES), "Roles must be independent addresses.")
    require(set(manifest.get("contracts", {})) == set(CONTRACT_NAMES), "Incomplete contract manifest.")
    contracts = manifest["contracts"]
    for name in CONTRACT_NAMES:
        entry = contracts[name]
        args = [] if name == "MockHKD" else [roles["deployerOwner"] if name == "PoGRegistryV2"
                                               else contracts["PoGRegistryV2"]["address"]]
        require(entry["constructorArguments"] == args, "Manifest constructor arguments mismatch.")
        info = information[name]
        require(entry["abi"] == {"path": info["abiPath"], "sha256": info["abiSha256"],
                                 "artifactId": info["artifactId"]}, "Manifest ABI hash/version mismatch.")
        require(entry["artifact"] == {"path": info["path"],
            "creationBytecodeKeccak256": command([cast, "keccak", info["artifact"]["bytecode"]["object"]]),
            "compilerVersion": "0.8.24+commit.e11b9ed9"}, "Manifest build artifact mismatch.")
        actual_receipt = verify_receipt(rpc, entry["deployment"])
        require(actual_receipt["contractAddress"].lower() == entry["address"].lower(), "Contract address/receipt mismatch.")
        require(runtime_record(rpc, cast, info, entry["address"], entry["deployment"],
                               roles["deployerOwner"], args) == entry["runtime"],
                "Manifest runtime hash/bytecode mismatch.")
        if name != "MockHKD":
            domain = eip712_domain(rpc, cast, entry["address"])
            require(domain == {"fields": 15, "name": name, "version": "2", "chainId": CHAIN_ID,
                               "verifyingContract": entry["address"].lower()}, "EIP712 V2 domain mismatch.")
    token = contracts["MockHKD"]["address"]
    registry = contracts["PoGRegistryV2"]["address"]
    escrow = contracts["ProcurementEscrowV2"]["address"]
    require(address_value(call_value(rpc, cast, registry, "owner()")) == roles["deployerOwner"].lower(), "Registry owner mismatch.")
    require(address_value(call_value(rpc, cast, registry, "escrow()")) == escrow.lower(), "Registry-to-Escrow binding mismatch.")
    require(address_value(call_value(rpc, cast, escrow, "registry()")) == registry.lower(), "Escrow-to-Registry binding mismatch.")
    require(int(call_value(rpc, cast, registry, "aiSigners(address)", roles["aiSigner"]), 16) == 1,
            "Configured development AI signer is not allowlisted.")
    require(int(call_value(rpc, cast, token, "decimals()"), 16) == 6, "MockHKD decimals mismatch.")
    require(string_value(call_value(rpc, cast, token, "name()")) == "Mock Hong Kong Dollar" and
            string_value(call_value(rpc, cast, token, "symbol()")) == "mHKD", "MockHKD token metadata mismatch.")
    bootstrap = manifest.get("bootstrap", {})
    require(bootstrap.get("tokenDecimals") == 6 and bootstrap.get("donorMintAtomic") == str(DONOR_MINT) and
            set(bootstrap.get("transactions", {})) == {"bindEscrow", "allowAISigner", "mintDonorA", "mintDonorB"},
            "Invalid bootstrap manifest.")
    expected_sends = {
        "bindEscrow": (registry, calldata(cast, "bindEscrow(address)", escrow)),
        "allowAISigner": (registry, calldata(cast, "setAISigner(address,bool)", roles["aiSigner"], "true")),
        "mintDonorA": (token, calldata(cast, "mint(address,uint256)", roles["donorA"], DONOR_MINT)),
        "mintDonorB": (token, calldata(cast, "mint(address,uint256)", roles["donorB"], DONOR_MINT)),
    }
    for name, (destination, data) in expected_sends.items():
        recorded = bootstrap["transactions"][name]
        verify_receipt(rpc, recorded)
        actual = rpc.call("eth_getTransactionByHash", [recorded["transactionHash"]])
        require(actual is not None and actual["to"].lower() == destination.lower() and
                actual["from"].lower() == roles["deployerOwner"].lower() and actual["input"].lower() == data.lower(),
                "Bootstrap transaction does not match the fixed mint/binding configuration.")
    balances = {name: str(int(call_value(rpc, cast, token, "balanceOf(address)", address), 16))
                for name, address in roles.items()}
    native_balances = {name: str(int(rpc.call("eth_getBalance", [address, "latest"]), 16))
                       for name, address in roles.items()}
    project_count = int(call_value(rpc, cast, registry, "projectCount()"), 16)
    owned_node(state, rpc)
    return {"status": "ready", "runId": state["runId"], "chainId": CHAIN_ID, "rpcUrl": rpc.url,
            "instanceId": state["instanceId"], "contracts": {name: entry["address"] for name, entry in contracts.items()},
            "roles": roles, "tokenBalancesAtomic": balances, "nativeBalancesWei": native_balances,
            "projectCount": project_count,
            "warning": "Valueless unlocked development accounts; no real HKD/exchange/AI/API deployed."}


def start(directory, port, forge, cast, anvil):
    require(not port_open(port), "Port occupied; refusing to adopt or stop another process.")
    require(bool(fingerprint(os.getpid())), "Process identity inspection unavailable; will not start an unmanaged node.")
    baselines()
    command([forge, "build"], timeout=120)
    information = artifacts()
    state_path, manifest_path = directory / "state.json", directory / "manifest.json"
    require(not state_path.exists() and not manifest_path.exists(), "Existing run records; use reset --confirm-reset.")
    state = {"schemaVersion": 1, "runId": uuid.uuid4().hex, "port": port,
             "rpcUrl": f"http://127.0.0.1:{port}", "chainId": CHAIN_ID, "phase": "launching",
             "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    log_path = directory / "anvil.log"
    safe_file(log_path)
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(log_fd, "w") as log:
        process = subprocess.Popen(anvil_arguments(anvil, port), cwd=ROOT, env=clean_env(), stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True)
    try:
        state["pid"] = process.pid
        state["processFingerprint"] = fingerprint(process.pid)
        require(bool(state["processFingerprint"]), "New Anvil process identity unavailable.")
        write_json(state_path, state)
    except Exception:
        # This Popen handle was created in this exact invocation, before any
        # ownership record exists. Do not leave a child after a recording error.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise
    rpc = Rpc(port)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        require(process.poll() is None, "Owned Anvil failed to start; inspect its local log.")
        try:
            metadata = rpc.call("anvil_metadata")
            require(process.poll() is None and process_matches(state), "Owned Anvil exited during startup.")
            require(int(rpc.call("eth_chainId"), 16) == CHAIN_ID, "Unexpected local chain ID.")
            state["instanceId"] = metadata["instanceId"]
            state["genesisHash"] = rpc.call("eth_getBlockByNumber", ["0x0", False])["hash"]
            break
        except NodeUnavailable:
            time.sleep(0.1)
    else:
        raise LocalError("Owned Anvil did not become ready; stop/reset explicitly.")
    state["phase"] = "bootstrapping"
    write_json(state_path, state)
    accounts = rpc.call("eth_accounts")
    require(len(accounts) == len(ROLE_NAMES), "Anvil must expose exactly 10 development accounts.")
    roles = dict(zip(ROLE_NAMES, accounts))
    contracts = {}
    for name in CONTRACT_NAMES:
        args = [] if name == "MockHKD" else [roles["deployerOwner"] if name == "PoGRegistryV2"
                                               else contracts["PoGRegistryV2"]["address"]]
        owned_node(state, rpc)
        address, receipt = deploy(rpc, forge, name, roles["deployerOwner"], args)
        info = information[name]
        contracts[name] = {"address": address, "constructorArguments": args, "deployment": receipt,
            "abi": {"path": info["abiPath"], "sha256": info["abiSha256"], "artifactId": info["artifactId"]},
            "artifact": {"path": info["path"],
                "creationBytecodeKeccak256": command([cast, "keccak", info["artifact"]["bytecode"]["object"]]),
                "compilerVersion": "0.8.24+commit.e11b9ed9"},
            "runtime": runtime_record(rpc, cast, info, address, receipt, roles["deployerOwner"], args)}
    token, registry, escrow = (contracts[name]["address"] for name in CONTRACT_NAMES)
    transactions = {}
    for name, destination, signature, args in (
        ("bindEscrow", registry, "bindEscrow(address)", [escrow]),
        ("allowAISigner", registry, "setAISigner(address,bool)", [roles["aiSigner"], "true"]),
        ("mintDonorA", token, "mint(address,uint256)", [roles["donorA"], DONOR_MINT]),
        ("mintDonorB", token, "mint(address,uint256)", [roles["donorB"], DONOR_MINT]),
    ):
        owned_node(state, rpc)
        transactions[name] = send(rpc, cast, roles["deployerOwner"], destination, signature, *args)
    manifest = {"schemaVersion": 1, "runId": state["runId"],
        "chain": {"chainId": CHAIN_ID, "rpcUrl": rpc.url, "genesisHash": state["genesisHash"], "instanceId": state["instanceId"]},
        "version": {"acceptedTag": ACCEPTED_TAG, "acceptedMerge": ACCEPTED_MERGE, "technicalSnapshot": SNAPSHOT},
        "foundry": {"version": FOUNDRY_VERSION, "commit": FOUNDRY_COMMIT, "hardfork": "cancun", "eip170LimitBytes": 24_576},
        "roles": roles, "contracts": contracts,
        "bootstrap": {"tokenDecimals": 6, "donorMintAtomic": str(DONOR_MINT), "transactions": transactions,
            "meaning": "Direct valueless development mint; not HKD receipt, conversion or a project donation."}}
    write_json(manifest_path, manifest)
    state["phase"] = "ready"
    write_json(state_path, state)
    report = verify(state, manifest, rpc, cast)
    require(report["projectCount"] == 0, "Bootstrap must not create a project.")
    require(report["tokenBalancesAtomic"] == {name: str(DONOR_MINT) if name in ("donorA", "donorB") else "0"
                                             for name in ROLE_NAMES}, "Unexpected bootstrap token balances.")
    report["manifestPath"] = str(manifest_path)
    return report


def stop(state, rpc):
    if not process_matches(state):
        pid = state.get("pid")
        require(not (isinstance(pid, int) and pid > 1 and fingerprint(pid)),
                "Recorded PID belongs to a mismatched live process; refusing stop/reset.")
        require(not port_open(state["port"]), "PID mismatch and occupied port; refusing to signal another process.")
        return
    if port_open(state["port"]):
        owned_node(state, rpc)
    # Recheck immediately before signalling: never kill a process by PID alone.
    require(process_matches(state), "PID identity changed; refusing to signal it.")
    os.kill(state["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not process_matches(state):
            require(not port_open(state["port"]), "Port still occupied after stopping own Anvil.")
            return
        time.sleep(0.1)
    raise LocalError("Owned process did not stop promptly; no SIGKILL fallback. Investigate manually.")


def archive(directory, state):
    destination = directory / "history" / state["runId"]
    require(not (directory / "history").is_symlink() and not destination.exists(), "Unsafe/existing history target.")
    destination.mkdir(parents=True, mode=0o700)
    for name in ("state.json", "manifest.json", "anvil.log"):
        source = directory / name
        safe_file(source)
        if source.exists():
            source.rename(destination / name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "status", "verify", "stop", "reset"))
    parser.add_argument("--port", type=int, default=8545)
    parser.add_argument("--state-dir", default=str(LOCAL_ROOT))
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args(argv)
    require(1024 <= args.port <= 65535, "Local port must be between 1024 and 65535.")
    require(args.action != "reset" or args.confirm_reset, "Reset requires --confirm-reset; this discards owned chain business state.")
    require(not args.confirm_reset or args.action == "reset", "--confirm-reset only applies to reset.")
    directory = safe_state_dir(args.state_dir)
    rpc = Rpc(args.port)
    with locked(directory), port_locked(args.port):
        state_path = directory / "state.json"
        state = read_json(state_path) if state_path.exists() else None
        if state:
            state_shape(state, args.port)
        if args.action in ("status", "stop") and state is None:
            require(not port_open(args.port), "Port occupied without owned state; refusing to adopt or stop it.")
            return {"status": "stopped", "rpcUrl": rpc.url, "manifestPath": str(directory / "manifest.json")}
        if args.action == "stop":
            stop(state, rpc)
            state["phase"] = "stopped"
            write_json(state_path, state)
            return {"status": "stopped", "runId": state["runId"], "recordsRetained": True,
                    "next": "up refuses stopped records; reset --confirm-reset creates a fresh chain."}
        if args.action == "status" and (not process_matches(state) or not port_open(args.port)):
            return {"status": "stopped" if state.get("phase") == "stopped" else "stale",
                    "runId": state["runId"], "rpcUrl": rpc.url,
                    "reason": "Owned node is not running; old addresses are not a live deployment."}
        if args.action == "up" and state is not None:
            require(state.get("phase") == "ready" and process_matches(state) and port_open(args.port),
                    "Existing stopped/incomplete/stale run; use reset --confirm-reset, never silently redeploy.")
        forge, cast, anvil = tool("forge"), tool("cast"), tool("anvil")
        if args.action in ("verify", "status") or (args.action == "up" and state is not None):
            require(state is not None, "No owned deployment; run up first.")
            try:
                report = verify(state, read_json(directory / "manifest.json"), rpc, cast)
            except LocalError as exc:
                if args.action == "status":
                    return {"status": "stale", "runId": state["runId"], "reason": str(exc)}
                raise
            report["manifestPath"] = str(directory / "manifest.json")
            return report
        if args.action == "reset":
            if state is not None:
                stop(state, rpc)
                archive(directory, state)
            else:
                require(not port_open(args.port), "Port occupied without owned state; reset will not touch another node.")
                require(not any((directory / name).exists() for name in ("manifest.json", "anvil.log")),
                        "Orphan records without owned state; preserve them and investigate.")
        return start(directory, args.port, forge, cast, anvil)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), indent=2))
    except (LocalError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f"local-chain: {error}", file=sys.stderr)
        sys.exit(1)
