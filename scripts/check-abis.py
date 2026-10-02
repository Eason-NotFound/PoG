#!/usr/bin/env python3
"""Check hand-off ABIs against the compiled contract interfaces (stdlib only)."""

import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    forge = sys.argv[1] if len(sys.argv) > 1 else "forge"
    root = Path(__file__).resolve().parent.parent
    entries = [
        ("MockHKD", root / "packages/contract-abis/MockHKD.json"),
        ("PoGRegistry", root / "packages/contract-abis/PoGRegistry.json"),
        ("MockHKD", root / "packages/contract-abis/v2/MockHKD.json"),
        ("PoGRegistryV2", root / "packages/contract-abis/v2/PoGRegistryV2.json"),
        ("ProcurementEscrowV2", root / "packages/contract-abis/v2/ProcurementEscrowV2.json"),
    ]
    for name, recorded_path in entries:
        contract = f"contracts/src/{name}.sol:{name}"
        generated = json.loads(
            subprocess.check_output([forge, "inspect", contract, "abi", "--json"], cwd=root)
        )
        recorded = json.loads(recorded_path.read_text())
        if generated != recorded:
            print(f"Stale ABI: {name}. Regenerate with the build runbook and review versioning.", file=sys.stderr)
            return 1
        print(f"ABI matches compiled interface: {recorded_path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
