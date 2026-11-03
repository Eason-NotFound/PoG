#!/usr/bin/env python3
"""Import two historical mock Markdown reports; never run code from a ZIP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 256 * 1024
STAGES = ("PRE", "FINAL")


class ImportFailure(ValueError):
    pass


def safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    parts = name.rstrip("/").split("/")
    if (not name or name.startswith("/") or "\\" in name or "\x00" in name
            or any(part in {"", ".", ".."} or ":" in part for part in parts)
            or stat.S_ISLNK(info.external_attr >> 16)):
        raise ImportFailure("Archive contains an unsafe member path or symlink")


def read_bundle(dataset_zip: Path) -> bytes:
    if dataset_zip.is_symlink() or not dataset_zip.is_file():
        raise ImportFailure("Dataset must be an existing local ZIP file, not a symlink")
    if dataset_zip.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ImportFailure("Dataset ZIP exceeds the import size limit")
    with zipfile.ZipFile(dataset_zip) as archive:
        infos = archive.infolist()
        if len(infos) > 5000:
            raise ImportFailure("Dataset ZIP contains too many members")
        names = set()
        for info in infos:
            safe_member(info)
            if info.filename in names:
                raise ImportFailure("Archive contains duplicate member names")
            names.add(info.filename)
        selected, prefixes = {}, set()
        for stage in STAGES:
            suffix = f"documents/stages/{stage}/ai_report.md"
            matches = [info for info in infos if info.filename == suffix or info.filename.endswith("/" + suffix)]
            if len(matches) != 1:
                raise ImportFailure(f"Expected exactly one {stage} Markdown report")
            info = matches[0]
            if info.is_dir() or info.flag_bits & 1 or not 0 < info.file_size <= MAX_REPORT_BYTES:
                raise ImportFailure(f"{stage} report is encrypted, empty or too large")
            prefixes.add(info.filename[:-len(suffix)])
            with archive.open(info) as stream:
                raw = stream.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ImportFailure(f"{stage} report exceeds the import size limit")
            try:
                markdown = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ImportFailure(f"{stage} report must be UTF-8 Markdown") from exc
            scores = re.findall(r"\bmockRiskScoreBps\s*=\s*([0-9]+)\b", markdown)
            if len(scores) != 1 or not 0 <= int(scores[0]) <= 10000:
                raise ImportFailure(f"{stage} report must declare one valid mockRiskScoreBps")
            if "simulat" not in markdown.lower():
                raise ImportFailure(f"{stage} report must be explicitly marked as simulated")
            heading = next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), None)
            selected[stage] = {"title": heading or f"{stage} historical mock reference",
                "sampleRiskScoreBps": int(scores[0]), "sourcePath": info.filename, "markdown": markdown}
        if len(prefixes) != 1:
            raise ImportFailure("PRE and FINAL must belong to the same dataset root")
    value = {"schema": "pog-offline-report-samples-v1", "sourcePackage": dataset_zip.name,
        "provenance": "mock_demo", "actualModelExecuted": False,
        "caseDescription": "Imported historical mock references; not a current-evidence assessment or model inference",
        "reports": selected}
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_BUNDLE_BYTES:
        raise ImportFailure("Encoded private report bundle exceeds the size limit")
    return encoded


def write_private_output(output: Path, raw: bytes) -> None:
    if output.suffix != ".json":
        raise ImportFailure("Private output must use a .json filename")
    if output.exists() or output.is_symlink():
        raise ImportFailure("Output already exists; refusing to overwrite it")
    if any(parent.is_symlink() for parent in output.parents):
        raise ImportFailure("Output parent paths must not be symlinks")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(output, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def import_reports(dataset_zip: Path, output: Path) -> None:
    raw = read_bundle(dataset_zip)
    write_private_output(output, raw)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-zip", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        import_reports(args.dataset_zip, args.output)
    except (ImportFailure, OSError, zipfile.BadZipFile, RuntimeError):
        # Do not echo archive content, report text, credentials or machine paths.
        print("Offline sample import failed; check safe ZIP stage files, mock metadata and a new private output path.", file=sys.stderr)
        return 2
    print("Imported private historical mock PRE/FINAL references; no model, database, chain or payment execution.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
