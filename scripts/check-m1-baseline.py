#!/usr/bin/env python3
"""Fail if accepted M1 files diverge from the preserved historical snapshot."""

import hashlib
from pathlib import Path
import sys


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    manifest = root / "docs/baselines/blockchain-m1.sha256"
    failures = []
    entries = manifest.read_text().splitlines()
    for entry in entries:
        expected, relative = entry.split(maxsplit=1)
        path = root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            failures.append(relative)
    if failures:
        print("Accepted M1 baseline changed: " + ", ".join(failures), file=sys.stderr)
        return 1
    print(f"Accepted M1 baseline preserved: {len(entries)}/{len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
