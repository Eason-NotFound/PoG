#!/usr/bin/env python3
"""Independent local M3.2 verifier; no production credentials or GitHub writes.

Requires a CLEAN, exact, PM-reviewed API candidate. Creates fresh owned Anvil,
PostgreSQL and actual loopback HTTP API processes. Never resets an existing node
or connects to an externally supplied database. All processes are stopped;
private ignored run records are retained. Not a booth or payment deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = "1844186df10854cd49ccc0886f5622e71e572d8e"
HANK = "bcd5de4daa565b79876a58391292e1b127f46a35"
PUBLISHED_003 = "services/api/migrations/versions/c31003a20003_signing_expiry.py"
PRESERVED = (
    "contracts", "foundry.toml", "packages/contract-abis", "docs/baselines",
    "scripts/local-chain.py", "scripts/check-blockchain.sh", "scripts/check-abis.py",
    "scripts/check-m1-baseline.py", "scripts/test-local-chain.py",
    "docs/M3_1_SPEC.md", "docs/M3_1_RUNBOOK.md", "docs/M3_1_REVIEW.md",
    "docs/M2_V2_SPEC.md", "docs/M2_V2_INTERFACE_IMPLEMENTED.md",
    "docs/M2_V2_INTEGRATION.md", "docs/M2_V2_RUNBOOK.md",
    ".github/workflows/blockchain.yml", ".github/workflows/local-chain.yml", ".githooks/pre-push",
)


class VerificationError(Exception):
    """Safe diagnostic that excludes credentials and response bodies."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def git(source: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(source), *args], capture_output=True,
                            text=True, timeout=15)
    require(result.returncode == 0, "Git snapshot check failed")
    return result.stdout.strip()


def check_candidate(source: Path, expected: str) -> dict:
    require(bool(re.fullmatch(r"[0-9a-f]{40}", expected)), "A full exact candidate SHA is required")
    require(source.is_dir() and not source.is_symlink(), "API source must be a real worktree")
    require(git(source, "rev-parse", "HEAD") == expected, "API HEAD differs from the reviewed candidate")
    require(not git(source, "status", "--porcelain", "--untracked-files=all"),
            "API candidate is dirty; freeze/review it before joint testing")
    require(git(source, "merge-base", BASE, expected) == BASE,
            "API candidate is not descended from accepted A1")
    require(git(source, "merge-base", HANK, expected) == HANK,
            "API candidate did not preserve Hank's published history")
    require(git(source, "rev-parse", f"{expected}:{PUBLISHED_003}") ==
            git(source, "rev-parse", f"{HANK}:{PUBLISHED_003}"),
            "Published migration 003 was rewritten")
    require(not git(source, "diff", "--name-only", BASE, expected, "--", *PRESERVED),
            "API candidate changed accepted blockchain artifacts")
    require((source / "services/api/src/pog_api/chain.py").is_file(), "A2 chain gateway is missing")
    migrations = source / "services/api/migrations/versions"
    require(len(list(migrations.glob("*0004*.py"))) == 1,
            "Reviewed candidate must retain published 003 and append one 004 migration")
    return {"apiCandidate": expected, "apiSource": str(source), "acceptedBase": BASE,
            "preservedHank": HANK, "published003Unchanged": True,
            "cleanSnapshot": True, "acceptedBlockchainUnchanged": True}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def clean_env() -> dict[str, str]:
    # No ambient proxy/libpq/chain/seed configuration may change owned targets.
    return {key: value for key, value in os.environ.items()
            if key in ("PATH", "HOME", "USER", "TMPDIR", "SYSTEMROOT", "LANG")}


def runtime_proof(source: Path, runtime: Path) -> dict:
    require(sys.version_info[:2] == (3, 13), "Run with the locked API Python 3.13 runtime")
    require(Path(sys.executable).resolve() == (runtime / "bin/python").resolve(),
            "Verifier Python must belong to the explicit project-local runtime")
    version = subprocess.run([str(runtime / "bin/postgres"), "--version"], capture_output=True,
                             text=True, env=clean_env(), timeout=10)
    require(version.returncode == 0 and re.fullmatch(r"postgres \(PostgreSQL\) 17\.\d+", version.stdout.strip()),
            "Project-local PostgreSQL 17 is required")
    from packaging.requirements import Requirement
    from packaging.markers import default_environment
    lock = source / "services/api/requirements.lock"
    requirements = [line.strip() for line in lock.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    versions = {}
    for value in requirements:
        requirement = Requirement(value)
        if requirement.marker is not None and not requirement.marker.evaluate(default_environment()):
            continue
        actual = importlib.metadata.version(requirement.name)
        require(actual in requirement.specifier, f"Installed {requirement.name} does not match the pinned candidate")
        versions[requirement.name] = actual
    return {"python": sys.version.split()[0], "postgres": version.stdout.strip(),
            "dependencies": versions,
            "lockSha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "pyprojectSha256": hashlib.sha256((source / "services/api/pyproject.toml").read_bytes()).hexdigest()}


def run_command(args: list[str], *, cwd: Path, env: dict[str, str], log: Path,
                timeout: int = 120, json_output: bool = False):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                            timeout=timeout)
    # Logs stay in an owned 0700 directory and are never printed or committed.
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    log.chmod(0o600)
    require(result.returncode == 0, f"{log.name} failed; inspect the private run log")
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def write_record(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def stop_child(process: subprocess.Popen | None) -> None:
    # This process handle came from this invocation; never look up/kill by port.
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def ready_matches(body: dict, manifest: dict, manifest_hash: str) -> bool:
    deployment = body.get("deployment", {})
    return (body.get("ready") is True and body.get("chainGate") == "verified"
            and deployment.get("chainVerified") is True
            and deployment.get("runId") == manifest["runId"]
            and deployment.get("instanceId") == manifest["chain"]["instanceId"]
            and deployment.get("chainId") == 31337
            and deployment.get("manifestSha256") == manifest_hash)


def start_api(source: Path, env: dict[str, str], run_dir: Path, label: str, owned_apis: list):
    manifest_path = Path(env["POG_A2_CHAIN_MANIFEST"])
    manifest = json.loads(manifest_path.read_text())
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    handle = (run_dir / f"api-{label}.log").open("w", encoding="utf-8")
    try:
        process = subprocess.Popen([sys.executable, "-m", "pog_api"], cwd=source,
                                   env=env, stdout=handle, stderr=handle)
    except BaseException:
        handle.close()
        raise
    # Register immediately, before readiness/imports can fail. Outer finally can
    # still retry exact-handle cleanup if startup's own cleanup raises.
    owned_apis.append((process, handle))
    import httpx
    base_url = f"http://127.0.0.1:{env['POG_BIND_PORT']}"
    try:
        with httpx.Client(base_url=base_url, trust_env=False, timeout=2) as client:
            for _ in range(120):
                require(process.poll() is None, "Official API process exited during startup")
                try:
                    response = client.get("/ready")
                    if response.status_code == 200 and ready_matches(response.json(), manifest, manifest_hash):
                        require(process.poll() is None, "Official API process no longer owns this startup")
                        return process, handle, base_url
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
        raise VerificationError("Official API did not become ready")
    except BaseException:
        try:
            stop_child(process)
        finally:
            handle.close()
        raise


def run_joint(source: Path, expected: str, runtime: Path, reset: bool) -> dict:
    proof = check_candidate(source, expected)
    require(sys.version_info[:2] == (3, 13), "Run with the locked API Python 3.13 runtime")
    require(runtime.is_dir(), "Project-local dependency runtime is required")
    for name in ("initdb", "pg_ctl", "postgres"):
        require((runtime / "bin" / name).is_file(), f"Runtime {name} is missing")
    dependencies = runtime_proof(source, runtime)
    # Keep state outside all working trees; /tmp/pog-local-* is accepted by M3.1.
    run_dir = Path(tempfile.mkdtemp(prefix="pog-local-m3-2-", dir="/tmp")).resolve()
    run_dir.chmod(0o700)
    pg_state = run_dir / "pg"
    pg_state.mkdir(mode=0o700)
    socket_dir = pg_state / "socket"
    socket_dir.mkdir(mode=0o700)
    data = pg_state / "data"
    chain_state = run_dir / "chain"
    ports = [free_port() for _ in range(3)]
    require(len(set(ports)) == 3 and all(p not in (8545, 18545, 55433) for p in ports),
            "Owned loopback port allocation collided; rerun")
    pg_port, chain_port, api_port = ports
    marker = {"owner": "pog-blockchain-m3-2", "invocation": uuid.uuid4().hex,
              "sourceCandidate": expected, "ports": ports}
    write_record(run_dir / "owner.json", marker)
    env = clean_env()
    env["POG_M3_2_INVOCATION"] = marker["invocation"]
    env["POG_M3_2_API_SHA"] = expected
    env["PYTHONPATH"] = str(source / "services/api/src") + os.pathsep + str(ROOT / "scripts")
    env["POG_DATABASE_URL"] = f"postgresql+psycopg://pog_api@127.0.0.1:{pg_port}/pog_api_test"
    env["POG_TEST_DATABASE_URL"] = env["POG_DATABASE_URL"]
    env["POG_M3_2_SCENARIO_DATABASE_URL"] = env["POG_DATABASE_URL"]
    env["POG_MANAGED_POSTGRES_STATE"] = str(pg_state)
    env["POG_STORAGE_ROOT"] = str(run_dir / "evidence")
    env["POG_A2_CHAIN_ENABLED"] = "true"
    env["POG_A2_DEMO_SIGNING_ENABLED"] = "true"
    env["POG_A2_CHAIN_MANIFEST"] = str(chain_state / "manifest.json")
    env["POG_BIND_HOST"], env["POG_BIND_PORT"] = "127.0.0.1", str(api_port)
    env["POG_M3_2_DEMO_PASSWORD"] = secrets.token_urlsafe(28)
    for name in ("FOUNDATION", "RECIPIENT", "DONOR", "ADMIN", "AI_FIXTURE", "DONOR_B"):
        env[f"POG_SEED_{name}_PASSWORD"] = env["POG_M3_2_DEMO_PASSWORD"]
    api_process = api_handle = None
    owned_apis = []
    pg_attempted = chain_attempted = False
    report = {**proof, "runDirectory": str(run_dir), "localOnly": True,
              "httpTransport": "actual loopback uvicorn; not TestClient",
              "postgresAuth": "isolated loopback trust fixture; not production",
              "stageLimit": "ReceiptConfirmed", "realAI": False, "payment": False}
    report["runtime"] = dependencies
    old_environment = dict(os.environ)
    try:
        run_command([str(runtime / "bin/initdb"), "-D", str(data), "-U", "pog_joint",
                     "--encoding=UTF8", "--locale=C", "--auth-local=trust", "--auth-host=trust"],
                    cwd=ROOT, env=env, log=run_dir / "pg-init.log")
        pg_attempted = True
        run_command([str(runtime / "bin/pg_ctl"), "-D", str(data), "-l", str(pg_state / "postgres.log"),
                     "-o", f"-h 127.0.0.1 -p {pg_port} -k {socket_dir}", "start", "-w"],
                    cwd=ROOT, env=env, log=run_dir / "pg-start.log")
        write_record(pg_state / "managed.json", {"managedBy": "pog-api-a1", "host": "127.0.0.1",
                     "port": pg_port, "testDatabase": "pog_api_test", "socket": str(socket_dir)})
        os.environ.clear()
        os.environ.update(env)
        sys.path.insert(0, str(source / "services/api/src"))
        from pog_api.test_database import assert_safe_test_target
        assert_safe_test_target(env["POG_DATABASE_URL"])
        import psycopg
        # Fresh-cluster initialization uses ONLY this invocation's owned socket.
        with psycopg.connect(host=str(socket_dir), port=pg_port, user="pog_joint",
                             dbname="postgres", autocommit=True) as connection:
            connection.execute("CREATE ROLE pog_api LOGIN")
            connection.execute("CREATE DATABASE pog_api_test OWNER pog_api")
        local_spec = importlib.util.spec_from_file_location("m3_1_owned", ROOT / "scripts/local-chain.py")
        local_chain = importlib.util.module_from_spec(local_spec)
        local_spec.loader.exec_module(local_chain)
        forge = local_chain.tool("forge")
        run_command([forge, "build"], cwd=source, env=env, log=run_dir / "api-artifact-build.log")
        chain_command = [sys.executable, str(ROOT / "scripts/local-chain.py")]
        chain_args = ["--port", str(chain_port), "--state-dir", str(chain_state)]
        chain_attempted = True
        deployment = run_command(chain_command + ["up", *chain_args], cwd=ROOT, env=env,
                                 log=run_dir / "chain-up.log", json_output=True)
        run_command([sys.executable, "-m", "alembic", "-c", "services/api/alembic.ini", "upgrade", "head"],
                    cwd=source, env=env, log=run_dir / "migrate.log")
        run_command([sys.executable, "-m", "pog_api.cli", "seed-chain-demo", "--confirm-chain-demo-fixtures"],
                    cwd=source, env=env, log=run_dir / "seed.log")
        api_process, api_handle, base_url = start_api(source, env, run_dir, "original", owned_apis)
        scenario = importlib.import_module("m3_2_scenario")
        scenario_proof, reset_context = scenario.run_scenario(
            base_url, env["POG_DATABASE_URL"], chain_state / "manifest.json", source, run_dir,
        )
        report["originalDeployment"] = {"runId": deployment["runId"], "chainId": 31337,
                                        "rpcPort": chain_port, "apiPort": api_port}
        report["scenario"] = scenario_proof
        if reset:
            # Explicit --run-with-reset affects ONLY the fresh owned node from this invocation.
            stop_child(api_process)
            api_process = None
            api_handle.close()
            api_handle = None
            fresh = run_command(chain_command + ["reset", *chain_args, "--confirm-reset"],
                                cwd=ROOT, env=env, log=run_dir / "chain-reset.log", json_output=True)
            require(fresh["runId"] != deployment["runId"], "Reset did not create a new run namespace")
            run_command([sys.executable, "-m", "pog_api.cli", "seed-chain-demo", "--confirm-chain-demo-fixtures"],
                        cwd=source, env=env, log=run_dir / "seed-fresh.log")
            api_process, api_handle, base_url = start_api(source, env, run_dir, "fresh", owned_apis)
            report["reset"] = scenario.check_reset(
                base_url, env["POG_DATABASE_URL"], chain_state / "manifest.json", source, run_dir,
                reset_context,
            )
        require(check_candidate(source, expected) == proof, "Candidate changed during verification")
        report["result"] = "passed"
    except BaseException as error:
        # Never persist exception values/response bodies that might contain secrets.
        report["result"] = "failed"
        report["failureClass"] = type(error).__name__
        print(f"Owned M3.2 records retained at {run_dir}", file=sys.stderr)
        raise
    finally:
        cleanup_errors = []
        for owned_process, owned_handle in reversed(owned_apis):
            try:
                stop_child(owned_process)
            except Exception:
                cleanup_errors.append("owned API stop failed; inspect owner record")
            finally:
                try:
                    owned_handle.close()
                except Exception:
                    cleanup_errors.append("owned API log close failed; inspect owner record")
        if chain_attempted:
            try:
                require(json.loads((run_dir / "owner.json").read_text()) == marker,
                        "Owned state marker changed")
                run_command([sys.executable, str(ROOT / "scripts/local-chain.py"), "stop",
                             "--port", str(chain_port), "--state-dir", str(chain_state)],
                            cwd=ROOT, env=env, log=run_dir / "chain-stop.log")
            except Exception:
                cleanup_errors.append("owned Anvil stop failed; inspect owner record")
        if pg_attempted:
            try:
                require(json.loads((run_dir / "owner.json").read_text()) == marker,
                        "Owned state marker changed")
                run_command([str(runtime / "bin/pg_ctl"), "-D", str(data), "stop", "-m", "fast", "-w"],
                            cwd=ROOT, env=env, log=run_dir / "pg-stop.log")
            except Exception:
                cleanup_errors.append("owned PostgreSQL stop failed; inspect owner record")
        report["cleanup"] = {"processesStopped": not cleanup_errors, "errors": cleanup_errors,
                             "recordsRetained": True}
        if cleanup_errors:
            report["result"] = "failed"
        try:
            write_record(run_dir / "verification.json", report)
        finally:
            os.environ.clear()
            os.environ.update(old_environment)
    require(report["cleanup"]["processesStopped"], "Owned process cleanup failed")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-source", type=Path, required=True)
    parser.add_argument("--api-sha", required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--pm-reviewed-sha", help="Explicit PM technical-review gate; must match API SHA")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--run", action="store_true")
    action.add_argument("--run-with-reset", action="store_true")
    args = parser.parse_args()
    source = args.api_source.absolute()
    try:
        if not (args.run or args.run_with_reset):
            report = check_candidate(source, args.api_sha)
        else:
            require(args.pm_reviewed_sha == args.api_sha, "PM-reviewed exact SHA must be explicitly supplied")
            require(args.runtime is not None, "Explicit project-local runtime is required")
            report = run_joint(source, args.api_sha, args.runtime.resolve(), args.run_with_reset)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (VerificationError, subprocess.TimeoutExpired) as error:
        print(f"M3.2 verification refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"M3.2 verification failed ({type(error).__name__}); inspect owned private logs", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
