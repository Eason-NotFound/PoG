"""Synthetic ZIP inputs only; no real report, credentials, DB or chain access."""
import importlib.util
import json
from pathlib import Path
import stat
import warnings
import zipfile

import pytest

from pog_api.offline_demo import load_reports

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_offline_reports.py"
spec = importlib.util.spec_from_file_location("offline_report_import", SCRIPT)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def fixture_zip(path, *, prefix="dataset/", extra=None, reports=None):
    if reports is None:
        reports = {stage:f"# {stage} unit-test sample\r\nSimulated output; NOT_RUN.\r\nmockRiskScoreBps={score}\r\n"
                   for stage,score in (("PRE",1600),("FINAL",1000))}
    with zipfile.ZipFile(path,"w") as archive:
        for stage,body in reports.items():
            archive.writestr(f"{prefix}documents/stages/{stage}/ai_report.md",body)
        for name,body in (extra or {}).items():
            archive.writestr(name,body)
    return path


def test_imported_bundle_matches_runtime_and_preserves_text(tmp_path):
    archive = fixture_zip(tmp_path / "unit-test.zip",extra={"scripts/do-not-run.py":"raise RuntimeError('never execute')"})
    output = tmp_path / "private" / "reports.private.json"
    importer.import_reports(archive,output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    value = json.loads(output.read_text())
    assert value["actualModelExecuted"] is False and value["provenance"] == "mock_demo"
    reports = load_reports(output)
    assert reports["PRE"]["sampleRiskScoreBps"] == 1600
    assert reports["FINAL"]["sampleRiskScoreBps"] == 1000
    assert reports["PRE"]["markdown"] == "# PRE unit-test sample\r\nSimulated output; NOT_RUN.\r\nmockRiskScoreBps=1600\r\n"
    assert len(list((tmp_path / "private").iterdir())) == 1


@pytest.mark.parametrize("unsafe",("../outside.txt","dataset/../outside.txt","/absolute.txt","C:/absolute.txt","a\\b.txt"))
def test_archive_unsafe_paths_fail_before_output(tmp_path,unsafe):
    archive = fixture_zip(tmp_path / "unit-test.zip",extra={unsafe:"ignored"})
    output = tmp_path / "reports.private.json"
    with pytest.raises(importer.ImportFailure): importer.import_reports(archive,output)
    assert not output.exists()


@pytest.mark.parametrize("fault",("missing","multiple","duplicate","roots","symlink"))
def test_stage_selection_is_unambiguous_and_cannot_follow_symlinks(tmp_path,fault):
    archive = fixture_zip(tmp_path / "unit-test.zip",reports={"PRE":"# Simulated\nmockRiskScoreBps=1600"} if fault=="missing" else None)
    with zipfile.ZipFile(archive,"a") as value, warnings.catch_warnings():
        warnings.simplefilter("ignore",UserWarning)
        if fault=="multiple": value.writestr("other/documents/stages/PRE/ai_report.md","Simulated mockRiskScoreBps=1600")
        if fault=="duplicate": value.writestr("dataset/documents/stages/PRE/ai_report.md","Simulated mockRiskScoreBps=1600")
        if fault=="symlink":
            entry = zipfile.ZipInfo("dataset/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            value.writestr(entry,"outside")
    if fault=="roots":
        with zipfile.ZipFile(archive,"w") as value:
            value.writestr("one/documents/stages/PRE/ai_report.md","Simulated mockRiskScoreBps=1600")
            value.writestr("two/documents/stages/FINAL/ai_report.md","Simulated mockRiskScoreBps=1000")
    with pytest.raises(importer.ImportFailure): importer.read_bundle(archive)


@pytest.mark.parametrize("body",("Simulated no score", "Simulated mockRiskScoreBps=10001",
    "Simulated mockRiskScoreBps=1 mockRiskScoreBps=2", "Real model mockRiskScoreBps=1600", "x"*65537))
def test_invalid_report_metadata_or_limits_fail(tmp_path,body):
    archive = fixture_zip(tmp_path / "unit-test.zip",reports={"PRE":body,"FINAL":"Simulated mockRiskScoreBps=1000"})
    with pytest.raises(importer.ImportFailure): importer.read_bundle(archive)


def test_existing_output_is_never_overwritten(tmp_path):
    archive = fixture_zip(tmp_path / "unit-test.zip")
    output = tmp_path / "reports.private.json"
    output.write_bytes(b"keep original")
    with pytest.raises(importer.ImportFailure): importer.import_reports(archive,output)
    assert output.read_bytes() == b"keep original"


def test_symlink_output_and_parent_are_rejected(tmp_path):
    archive = fixture_zip(tmp_path / "unit-test.zip")
    original = tmp_path / "original.json"
    original.write_bytes(b"keep original")
    output = tmp_path / "reports.private.json"
    output.symlink_to(original)
    with pytest.raises(importer.ImportFailure): importer.import_reports(archive,output)
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real_dir,target_is_directory=True)
    with pytest.raises(importer.ImportFailure): importer.import_reports(archive,linked / "new.json")
    assert original.read_bytes() == b"keep original" and not (real_dir / "new.json").exists()


def test_cli_failure_is_redacted(tmp_path,capsys):
    assert importer.main(["--dataset-zip",str(tmp_path / "missing-secret-location.zip"),
        "--output",str(tmp_path / "reports.private.json")]) == 2
    captured = capsys.readouterr()
    assert "missing-secret-location" not in captured.err and captured.out == ""
