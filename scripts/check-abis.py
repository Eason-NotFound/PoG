#!/usr/bin/env python3
"""Check hand-off ABIs against the compiled contract interfaces (stdlib only)."""

import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    forge = sys.argv[1] if len(sys.argv) > 1 else "forge"
    root = Path(__file__).resolve().parent.parent
    for name in ("MockHKD", "PoGRegistry"):
        contract = f"contracts/src/{name}.sol:{name}"
        generated = json.loads(
            subprocess.check_output([forge, "inspect", contract, "abi", "--json"], cwd=root)
        )
        recorded = json.loads((root / "packages" / "contract-abis" / f"{name}.json").read_text())
        if generated != recorded:
            print(f"Stale ABI: {name}. Regenerate with the build runbook and review versioning.", file=sys.stderr)
            return 1
        print(f"ABI matches compiled interface: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
