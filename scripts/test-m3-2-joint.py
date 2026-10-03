#!/usr/bin/env python3
"""Pure fail-closed tests; no database, RPC or external process mutation."""
import importlib.util
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("joint", Path(__file__).with_name("verify-m3-2-joint.py"))
joint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(joint)


class CandidateGateTests(unittest.TestCase):
    sha = "a" * 40

    def test_short_sha_rejected_before_git(self):
        with patch.object(joint, "git") as command:
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, "abcd")
            command.assert_not_called()

    def test_wrong_head_rejected(self):
        with patch.object(joint, "git", return_value="b" * 40):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_dirty_candidate_rejected(self):
        with patch.object(joint, "git", side_effect=[self.sha, " M source.py"]):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_wrong_base_rejected(self):
        with patch.object(joint, "git", side_effect=[self.sha, "", "b" * 40]):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_changed_contract_rejected(self):
        with patch.object(joint, "git", side_effect=[self.sha, "", joint.BASE, joint.HANK,
                                                    "same-blob", "same-blob", "contracts/src/MockHKD.sol"]):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_missing_hank_history_rejected(self):
        with patch.object(joint, "git", side_effect=[self.sha, "", joint.BASE, joint.BASE]):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_changed_published_migration_rejected(self):
        with patch.object(joint, "git", side_effect=[self.sha, "", joint.BASE, joint.HANK,
                                                    "new-blob", "published-blob"]):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_root_config_and_abis_protected(self):
        self.assertIn("foundry.toml", joint.PRESERVED)
        self.assertIn("packages/contract-abis", joint.PRESERVED)
        self.assertIn("docs/baselines", joint.PRESERVED)

    def test_clean_review_snapshot_passes_read_only_gate(self):
        answers = [self.sha, "", joint.BASE, joint.HANK, "published-blob", "published-blob", ""]
        with patch.object(joint, "git", side_effect=answers), patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "glob", return_value=iter([Path("only-004.py")])):
            proof = joint.check_candidate(joint.ROOT, self.sha)
        self.assertEqual(proof["apiCandidate"], self.sha)
        self.assertTrue(proof["published003Unchanged"] and proof["cleanSnapshot"])

    def test_duplicate_004_is_rejected(self):
        answers = [self.sha, "", joint.BASE, joint.HANK, "published-blob", "published-blob", ""]
        with patch.object(joint, "git", side_effect=answers), patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "glob", return_value=iter([Path("first-004.py"), Path("second-004.py")])):
            with self.assertRaises(joint.VerificationError):
                joint.check_candidate(joint.ROOT, self.sha)

    def test_readiness_binds_exact_run(self):
        manifest = {"runId": "fresh", "chain": {"instanceId": "new-instance"}}
        body = {"ready": True, "chainGate": "verified", "deployment": {
            "chainVerified": True, "runId": "fresh", "instanceId": "new-instance",
            "chainId": 31337, "manifestSha256": "abc",
        }}
        self.assertTrue(joint.ready_matches(body, manifest, "abc"))
        body["deployment"]["runId"] = "old"
        self.assertFalse(joint.ready_matches(body, manifest, "abc"))

    def test_http_200_unverified_is_not_readiness(self):
        manifest = {"runId": "fresh", "chain": {"instanceId": "new-instance"}}
        self.assertFalse(joint.ready_matches({"ready": True}, manifest, "abc"))

    def test_ambient_targets_removed(self):
        with patch.dict(joint.os.environ, {"PGHOST": "elsewhere", "HTTPS_PROXY": "evil", "POG_DATABASE_URL": "unsafe"}):
            env = joint.clean_env()
        for name in ("PGHOST", "HTTPS_PROXY", "POG_DATABASE_URL"):
            self.assertNotIn(name, env)

    def test_no_kill_by_port(self):
        process = unittest.mock.Mock()
        process.poll.return_value = None
        joint.stop_child(process)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        process.kill.assert_not_called()

    def test_exited_child_not_touched(self):
        process = unittest.mock.Mock()
        process.poll.return_value = 0
        joint.stop_child(process)
        process.terminate.assert_not_called()

    def test_owned_child_timeout_bounded(self):
        process = unittest.mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("owned", 10), 0]
        joint.stop_child(process)
        process.kill.assert_called_once()


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix="pog-m3-2-unit-runtime-", dir="/tmp")
        self.runtime = Path(self.fixture.name)
        (self.runtime / "bin").mkdir()
        for name in ("initdb", "pg_ctl", "postgres"):
            (self.runtime / "bin" / name).touch()

    def tearDown(self):
        self.fixture.cleanup()

    def _run_patches(self):
        return (
            patch.object(joint, "check_candidate", return_value={"apiCandidate": "a" * 40}),
            patch.object(joint, "runtime_proof", return_value={"fixture": True}),
            patch.object(joint.sys, "version_info", (3, 13, 15)),
        )

    def test_pg_partial_start_failure_still_attempts_owned_stop(self):
        commands = []
        def command(args, **kwargs):
            commands.append(args)
            if "start" in args:
                raise subprocess.TimeoutExpired("owned pg start", 120)
            return ""
        patches = self._run_patches()
        with patches[0], patches[1], patches[2], patch.object(joint, "run_command", side_effect=command), \
                patch.object(joint, "free_port", side_effect=[40111, 40112, 40113]):
            with self.assertRaises(subprocess.TimeoutExpired):
                joint.run_joint(joint.ROOT, "a" * 40, self.runtime, False)
        stops = [args for args in commands if "stop" in args]
        self.assertEqual(len(stops), 1)
        self.assertIn("pg_ctl", stops[0][0])
        self.assertIn("-D", stops[0])
        self.assertNotIn("8545", " ".join(stops[0]))

    def test_guard_and_clean_parent_environment_precede_first_driver_connection(self):
        events = []
        guard_module = types.ModuleType("pog_api.test_database")
        guard_module.assert_safe_test_target = lambda value: events.append("guard")
        driver = types.ModuleType("psycopg")
        def connect(**kwargs):
            events.append("connect")
            self.assertEqual(events, ["guard", "connect"])
            socket_path = Path(kwargs["host"])
            self.assertTrue(socket_path.is_relative_to(Path("/tmp").resolve()))
            self.assertTrue(any(part.startswith("pog-local-m3-2-") for part in socket_path.parts))
            self.assertNotIn("PGHOSTADDR", os.environ)
            self.assertNotIn("PGSERVICE", os.environ)
            raise joint.VerificationError("fixture stops before any connection")
        driver.connect = connect
        patches = self._run_patches()
        original = dict(os.environ)
        with patches[0], patches[1], patches[2], patch.object(joint, "run_command", return_value=""), \
                patch.object(joint, "free_port", side_effect=[40111, 40112, 40113]), \
                patch.dict(sys.modules, {"pog_api.test_database": guard_module, "psycopg": driver}), \
                patch.dict(os.environ, {"PGHOSTADDR": "203.0.113.77", "PGSERVICE": "unsafe"}):
            expected = dict(os.environ)
            with self.assertRaises(joint.VerificationError):
                joint.run_joint(joint.ROOT, "a" * 40, self.runtime, False)
            self.assertTrue(dict(os.environ) == expected, "Parent environment was not restored (values redacted)")
        self.assertTrue(dict(os.environ) == original, "Fixture environment changed (values redacted)")
        self.assertEqual(events, ["guard", "connect"])


if __name__ == "__main__":
    unittest.main()
