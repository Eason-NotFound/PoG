#!/usr/bin/env python3
"""Exercise the push guard in an isolated temporary Git repository."""

from pathlib import Path
import subprocess
import tempfile


def main() -> None:
    hook = Path(__file__).resolve().parent.parent / ".githooks" / "pre-push"
    destination = "https://github.com/Eason-NotFound/PoG.git"
    zero = "0" * 40
    with tempfile.TemporaryDirectory(prefix="pog-push-guard-") as directory:
        def git(*args: str) -> str:
            return subprocess.check_output(["git", *args], cwd=directory, stderr=subprocess.PIPE).decode().strip()

        git("init", "--quiet")
        git("-c", "user.name=Hook Test", "-c", "user.email=hook-test@example.invalid",
            "commit", "--quiet", "--allow-empty", "-m", "test ancestor")
        ancestor = git("rev-parse", "HEAD")
        git("-c", "user.name=Hook Test", "-c", "user.email=hook-test@example.invalid",
            "commit", "--quiet", "--allow-empty", "-m", "test descendant")
        descendant = git("rev-parse", "HEAD")

        cases = [
            ("new feature branch", "origin", destination,
             f"refs/heads/codex/test {descendant} refs/heads/codex/test {zero}", True),
            ("fast-forward branch", "origin", destination,
             f"refs/heads/codex/test {descendant} refs/heads/codex/test {ancestor}", True),
            ("main creation blocked", "origin", destination,
             f"refs/heads/codex/test {descendant} refs/heads/main {zero}", False),
            ("empty root main bootstrap", "origin", destination,
             f"refs/heads/bootstrap {ancestor} refs/heads/main {zero}", True),
            ("main update blocked", "origin", destination,
             f"refs/heads/codex/test {descendant} refs/heads/main {ancestor}", False),
            ("branch rewind blocked", "origin", destination,
             f"refs/heads/codex/test {ancestor} refs/heads/codex/test {descendant}", False),
            ("new version tag", "origin", destination,
             f"refs/tags/blockchain-vtest {descendant} refs/tags/blockchain-vtest {zero}", True),
            ("version tag update blocked", "origin", destination,
             f"refs/tags/blockchain-vtest {descendant} refs/tags/blockchain-vtest {ancestor}", False),
            ("version tag delete blocked", "origin", destination,
             f"(delete) {zero} refs/tags/blockchain-vtest {ancestor}", False),
            ("branch delete blocked", "origin", destination,
             f"(delete) {zero} refs/heads/codex/test {ancestor}", False),
            ("wrong destination blocked", "origin", "https://github.com/example/other.git",
             f"refs/heads/codex/test {descendant} refs/heads/codex/test {zero}", False),
            ("wrong remote blocked", "other", destination,
             f"refs/heads/codex/test {descendant} refs/heads/codex/test {zero}", False),
        ]
        for label, remote, url, refs, should_pass in cases:
            result = subprocess.run([str(hook), remote, url], input=refs + "\n", text=True,
                                    capture_output=True, cwd=directory, check=False)
            if (result.returncode == 0) != should_pass:
                raise RuntimeError(f"{label}: unexpected result: {result.stderr}")
            print(f"PASS: {label}")
        print(f"Push guard: {len(cases)}/{len(cases)} cases passed")


if __name__ == "__main__":
    main()
