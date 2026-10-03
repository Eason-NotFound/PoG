#!/usr/bin/env python3
"""Pure runner recovery tests: every process, connection and port is mocked.

Own temporary directories contain only dummy runtime files and safe JSON proof
records. These tests never execute PostgreSQL/Anvil/HTTP or use team databases.
"""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "joint_recovery", Path(__file__).with_name("verify-m3-2-joint.py")
)
joint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint)


class RunnerRecoveryTests(unittest.TestCase):
    sha = "a" * 40

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pog-runner-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.fixture = Path(self.temporary.name).resolve()
        self.source = self.fixture / "candidate"
        self.source.mkdir()
        self.runtime = self.fixture / "runtime"
        (self.runtime / "bin").mkdir(parents=True)
        for name in ("initdb", "pg_ctl", "postgres"):
            (self.runtime / "bin" / name).touch()
        self.run_dir = self.fixture / "owned-run"
        self.run_dir.mkdir(mode=0o700)
        self.events = []
        self.proof = {"apiCandidate": self.sha, "cleanSnapshot": True}
        self.process = Mock(name="owned_mock_api")
        self.handle = Mock(name="owned_mock_api_log")
        self.parent_environment = dict(os.environ)
        self.parent_path = list(sys.path)
        self.addCleanup(setattr, sys, "path", self.parent_path)
        self.guard_failure = False
        self.actual_start_api = joint.start_api

    def command(self, args, **kwargs):
        """Never delegate commands to a real subprocess implementation."""
        if Path(args[0]).name == "initdb":
            action = "pg-init"
        elif Path(args[0]).name == "pg_ctl":
            action = "pg-stop" if "stop" in args else "pg-start"
        elif "local-chain.py" in " ".join(args):
            action = "chain-stop" if "stop" in args else "chain-up"
        elif args[1:] == ["build"]:
            action = "candidate-build"
        elif "alembic" in args:
            action = "migrate"
        else:
            action = "seed"
        self.events.append(action)
        if action in getattr(self, "failing_commands", {}):
            raise self.failing_commands[action]
        if action == "chain-up":
            chain_dir = self.run_dir / "chain"
            chain_dir.mkdir(mode=0o700)
            joint.write_record(chain_dir / "manifest.json", {
                "runId": "unit-run", "chain": {"instanceId": "unit-instance"},
            })
            return {"runId": "unit-run"}
        return ""

    def guard(self, url):
        self.events.append("guard")
        marker = json.loads((self.run_dir / "pg/managed.json").read_text())
        self.assertEqual(marker["port"], 40111)
        self.assertEqual(url, "postgresql+psycopg://pog_api@127.0.0.1:40111/pog_api_test")
        self.assertNotIn("PGHOSTADDR", os.environ)
        if self.guard_failure:
            raise joint.VerificationError("unit guard refuses target")

    def connect(self, **kwargs):
        self.events.append("connect")
        self.assertIn("guard", self.events)
        self.assertLess(self.events.index("guard"), self.events.index("connect"))
        self.assertEqual(kwargs["host"], str(self.run_dir / "pg/socket"))
        self.assertEqual(kwargs["port"], 40111)
        connection = Mock(name="never_connected_driver")
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        return connection

    def start_api(self, source, env, run_dir, label, owned_apis):
        self.events.append("api-start")
        if getattr(self, "actual_mocked_startup", False):
            self.registered_apis = owned_apis
            return self.actual_start_api(source, env, run_dir, label, owned_apis)
        owned_apis.append((self.process, self.handle))
        return self.process, self.handle, "http://127.0.0.1:40113"

    def stop_api(self, process):
        if process is not None:
            self.assertIs(process, self.process)
            self.events.append("api-stop")
            if getattr(self, "api_stop_failure", False) or (
                getattr(self, "api_stop_failure_once", False)
                and self.events.count("api-stop") == 1
            ):
                raise OSError("unit owned API stop failure")

    def scenario(self, *args):
        self.events.append("scenario")
        if getattr(self, "scenario_failure", False):
            raise joint.VerificationError("unit scenario refused")
        if getattr(self, "owner_marker_changed", False):
            joint.write_record(self.run_dir / "owner.json", {"owner": "not-this-invocation"})
        return {"state": "ReceiptConfirmed", "fixtureOnly": True}, {}

    def run_mocked(self, *, record_failure=False):
        guard_module = types.ModuleType("pog_api.test_database")
        guard_module.assert_safe_test_target = self.guard
        package = types.ModuleType("pog_api")
        package.__path__ = []
        driver = types.ModuleType("psycopg")
        driver.connect = self.connect
        httpx_module = types.ModuleType("httpx")
        httpx_module.TransportError = OSError
        client = Mock(name="never_connected_http_client")
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get.side_effect = AssertionError("Unexpected HTTP request")
        httpx_module.Client = Mock(return_value=client)
        local_module = types.SimpleNamespace(tool=lambda name: "/never-executed/forge")
        scenario_module = types.SimpleNamespace(run_scenario=self.scenario)
        original_record = joint.write_record

        def record(path, value):
            if path.name == "verification.json" and record_failure:
                self.events.append("record-failed")
                raise OSError("unit owned report write failure")
            original_record(path, value)

        with ExitStack() as stack:
            stack.enter_context(patch.object(joint, "check_candidate", return_value=self.proof))
            stack.enter_context(patch.object(joint, "runtime_proof", return_value={"fixtureOnly": True}))
            stack.enter_context(patch.object(joint.sys, "version_info", (3, 13, 15)))
            stack.enter_context(patch.object(joint.tempfile, "mkdtemp", return_value=str(self.run_dir)))
            stack.enter_context(patch.object(joint, "free_port", side_effect=[40111, 40112, 40113]))
            stack.enter_context(patch.object(joint, "run_command", side_effect=self.command))
            stack.enter_context(patch.object(joint, "start_api", side_effect=self.start_api))
            stack.enter_context(patch.object(joint, "stop_child", side_effect=self.stop_api))
            stack.enter_context(patch.object(joint, "write_record", side_effect=record))
            stack.enter_context(patch.object(joint.importlib.util, "spec_from_file_location", return_value=Mock()))
            stack.enter_context(patch.object(joint.importlib.util, "module_from_spec", return_value=local_module))
            stack.enter_context(patch.object(joint.importlib, "import_module", return_value=scenario_module))
            stack.enter_context(patch.dict(sys.modules, {
                "pog_api": package, "pog_api.test_database": guard_module,
                "psycopg": driver, "httpx": httpx_module,
            }))
            # A last safety tripwire: any unmocked command/process start fails.
            stack.enter_context(patch.object(joint.subprocess, "run", side_effect=AssertionError("Unexpected real command")))
            if getattr(self, "actual_mocked_startup", False):
                stack.enter_context(patch.object(joint.subprocess, "Popen", return_value=self.process))
            else:
                stack.enter_context(patch.object(joint.subprocess, "Popen", side_effect=AssertionError("Unexpected real process")))
            stack.enter_context(patch.dict(os.environ, {"PGHOSTADDR": "203.0.113.77"}))
            expected_environment = dict(os.environ)
            try:
                return joint.run_joint(self.source, self.sha, self.runtime, False)
            finally:
                self.assertTrue(dict(os.environ) == expected_environment,
                                "Parent environment was not restored; values are redacted")

    def record(self):
        return json.loads((self.run_dir / "verification.json").read_text())

    def assert_owned_cleanup(self):
        self.assertIn("chain-stop", self.events)
        self.assertIn("pg-stop", self.events)
        self.assertLess(self.events.index("chain-stop"), self.events.index("pg-stop"))
        self.assertTrue(dict(os.environ) == self.parent_environment,
                        "Parent environment changed; values are redacted")

    def test_partial_anvil_start_still_stops_owned_chain_and_postgres(self):
        self.failing_commands = {"chain-up": subprocess.TimeoutExpired("unit owned Anvil", 120)}
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_mocked()
        self.assert_owned_cleanup()
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["failureClass"], "TimeoutExpired")
        self.assertTrue(record["cleanup"]["processesStopped"])
        self.assertNotIn("api-start", self.events)

    def test_api_cleanup_failure_does_not_skip_chain_pg_or_record(self):
        self.api_stop_failure = True
        with self.assertRaises(joint.VerificationError):
            self.run_mocked()
        self.assert_owned_cleanup()
        self.assertIn("api-stop", self.events)
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertFalse(record["cleanup"]["processesStopped"])
        self.assertEqual(record["cleanup"]["errors"], ["owned API stop failed; inspect owner record"])

    def test_scenario_failure_still_closes_all_owned_resources(self):
        self.scenario_failure = True
        with self.assertRaises(joint.VerificationError):
            self.run_mocked()
        self.assert_owned_cleanup()
        self.assertIn("api-stop", self.events)
        self.handle.close.assert_called_once()
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertTrue(record["cleanup"]["processesStopped"])

    def test_report_write_failure_still_restores_environment_after_cleanup(self):
        with self.assertRaises(OSError):
            self.run_mocked(record_failure=True)
        self.assert_owned_cleanup()
        self.assertIn("record-failed", self.events)

    def test_anvil_cleanup_failure_does_not_skip_pg(self):
        self.failing_commands = {"chain-stop": joint.VerificationError("unit Anvil stop refused")}
        with self.assertRaises(joint.VerificationError):
            self.run_mocked()
        self.assert_owned_cleanup()
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertFalse(record["cleanup"]["processesStopped"])
        self.assertEqual(record["cleanup"]["errors"], ["owned Anvil stop failed; inspect owner record"])

    def test_api_log_close_failure_still_attempts_chain_pg_cleanup(self):
        self.handle.close.side_effect = OSError("unit log close failure")
        with self.assertRaises(joint.VerificationError):
            self.run_mocked()
        self.assert_owned_cleanup()
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertFalse(record["cleanup"]["processesStopped"])
        self.assertEqual(record["cleanup"]["errors"], ["owned API log close failed; inspect owner record"])

    def test_changed_owner_marker_refuses_chain_pg_signals_and_records_failure(self):
        self.owner_marker_changed = True
        with self.assertRaises(joint.VerificationError):
            self.run_mocked()
        self.assertNotIn("chain-stop", self.events)
        self.assertNotIn("pg-stop", self.events)
        self.assertIn("api-stop", self.events)
        self.assertTrue(dict(os.environ) == self.parent_environment,
                        "Parent environment changed; values are redacted")
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertFalse(record["cleanup"]["processesStopped"])
        self.assertEqual(record["cleanup"]["errors"], [
            "owned Anvil stop failed; inspect owner record",
            "owned PostgreSQL stop failed; inspect owner record",
        ])

    def test_partial_pg_start_failure_still_attempts_owned_pg_stop(self):
        self.failing_commands = {"pg-start": subprocess.TimeoutExpired("unit owned PostgreSQL", 120)}
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_mocked()
        self.assertIn("pg-stop", self.events)
        self.assertNotIn("chain-up", self.events)
        self.assertNotIn("connect", self.events)
        self.assertEqual(self.record()["result"], "failed")
        self.assertTrue(dict(os.environ) == self.parent_environment,
                        "Parent environment changed; values are redacted")

    def test_guard_failure_prevents_first_driver_connection(self):
        self.guard_failure = True
        with self.assertRaises(joint.VerificationError):
            self.run_mocked()
        self.assertIn("guard", self.events)
        self.assertNotIn("connect", self.events)
        self.assertIn("pg-stop", self.events)
        self.assertNotIn("chain-up", self.events)
        self.assertTrue(self.record()["cleanup"]["processesStopped"])

    def test_mocked_success_records_candidate_build_before_chain(self):
        report = self.run_mocked()
        self.assert_owned_cleanup()
        self.assertLess(self.events.index("candidate-build"), self.events.index("chain-up"))
        self.assertEqual(report["result"], "passed")
        self.assertTrue(report["cleanup"]["processesStopped"])
        self.assertEqual(report["runtime"], {"fixtureOnly": True})

    def prepare_early_startup_failure(self):
        self.actual_mocked_startup = True

        def poll():
            self.assertEqual(len(self.registered_apis), 1)
            self.assertIs(self.registered_apis[0][0], self.process)
            self.assertFalse(self.registered_apis[0][1].closed)
            return 1

        self.process.poll.side_effect = poll

    def test_unready_api_registered_immediately_and_cleanup_retried(self):
        self.prepare_early_startup_failure()
        self.api_stop_failure_once = True
        with self.assertRaises(OSError):
            self.run_mocked()
        self.assert_owned_cleanup()
        self.assertEqual(self.events.count("api-stop"), 2)
        self.assertTrue(self.registered_apis[0][1].closed)
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["failureClass"], "OSError")
        self.assertTrue(record["cleanup"]["processesStopped"])
        self.assertNotIn("scenario", self.events)

    def test_unready_api_repeated_cleanup_failure_is_not_claimed_stopped(self):
        self.prepare_early_startup_failure()
        self.api_stop_failure = True
        with self.assertRaises(OSError):
            self.run_mocked()
        self.assert_owned_cleanup()
        self.assertEqual(self.events.count("api-stop"), 2)
        self.assertTrue(self.registered_apis[0][1].closed)
        record = self.record()
        self.assertEqual(record["result"], "failed")
        self.assertFalse(record["cleanup"]["processesStopped"])
        self.assertEqual(record["cleanup"]["errors"], ["owned API stop failed; inspect owner record"])


if __name__ == "__main__":
    unittest.main()
