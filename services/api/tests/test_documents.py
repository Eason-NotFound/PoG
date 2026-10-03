from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from conftest import auth
from pog_api.app import create_app
from pog_api.errors import APIError
from pog_api.file_store import MAX_FILE_BYTES, PrivateFileStore
from pog_api.models import Document, DocumentVersion, Operation


def valid_pdf() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def valid_image(kind: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(20, 40, 60)).save(output, format=kind)
    return output.getvalue()


def upload(client, token, procurement_id, key, data, filename, content_type):
    return client.post(
        "/v2/documents",
        data={"procurementId": procurement_id, "category": "invoice"},
        files={"file": (filename, data, content_type)},
        headers={**auth(token), "Idempotency-Key": key},
    )


def test_valid_pdf_png_jpeg_upload_hash_and_private_permissions(
    client, created_procurement, actors, login, settings
):
    token, project, procurement = created_procurement
    procurement_id = procurement["procurement"]["id"]
    samples = [
        (valid_pdf(), "invoice.pdf", "application/pdf"),
        (valid_image("PNG"), "goods.png", "image/png"),
        (valid_image("JPEG"), "goods.jpg", "image/jpeg"),
    ]
    ids = []
    for index, sample in enumerate(samples):
        response = upload(client, token, procurement_id, f"file-{index}", *sample)
        assert response.status_code == 202, response.text
        body = response.json()
        ids.append(body["document"]["id"])
        assert len(body["document"]["sha256"]) == 64
        assert len(body["document"]["keccak256"]) == 64
        assert body["document"]["abiCombinationKeccak"] is None
        assert body["operation"]["chainVerified"] is False
    stored_files = [path for path in settings.storage_root.rglob("*.bin")]
    assert len(stored_files) == 3
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in stored_files)
    recipient_token = login(actors["recipient"])
    assert (
        client.get(f"/v2/documents/{ids[0]}/content", headers=auth(recipient_token)).status_code
        == 200
    )
    donor_token = login(actors["donor"])
    denied = client.get(f"/v2/documents/{ids[0]}/content", headers=auth(donor_token))
    assert denied.status_code == 403


def test_truncated_and_magic_only_files_are_rejected_and_cleaned(
    client, created_procurement, settings
):
    token, _project, procurement = created_procurement
    pid = procurement["procurement"]["id"]
    invalid = [
        (b"%PDF-not-a-document", "bad.pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "bad.png", "image/png"),
        (b"\xff\xd8\xffgarbage\xff\xd9", "bad.jpg", "image/jpeg"),
    ]
    for index, sample in enumerate(invalid):
        response = upload(client, token, pid, f"bad-{index}", *sample)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_file_content"
    assert list(settings.storage_root.rglob("*.upload")) == []
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_extension_mime_and_path_traversal_rejected(client, created_procurement, settings):
    token, _project, procurement = created_procurement
    pid = procurement["procurement"]["id"]
    mismatch = upload(client, token, pid, "mismatch", valid_pdf(), "invoice.png", "application/pdf")
    assert mismatch.status_code == 422
    traversal = upload(
        client, token, pid, "traversal", valid_pdf(), "../invoice.pdf", "application/pdf"
    )
    assert traversal.status_code == 400
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_unknown_procurement_cleans_staged_file(
    client, actors, login, settings, session_factory
):
    token = login(actors["foundation"])
    response = upload(
        client,
        token,
        "00000000-0000-0000-0000-000000000001",
        "unknown-parent",
        valid_pdf(),
        "invoice.pdf",
        "application/pdf",
    )
    assert response.status_code == 404
    operation_id = response.json()["error"]["operationId"]
    query = client.get(f"/v2/operations/{operation_id}", headers=auth(token))
    assert query.status_code == 200
    assert query.json()["status"] == "failed"
    assert query.json()["errorStatus"] == 404
    assert list(settings.storage_root.rglob("*.upload")) == []
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_upload_idempotency_restart_and_old_content_immutable(
    client, created_procurement, settings, session_factory
):
    token, _project, procurement = created_procurement
    pid = procurement["procurement"]["id"]
    data = valid_pdf()
    first = upload(client, token, pid, "same-upload", data, "invoice.pdf", "application/pdf")
    second = upload(client, token, pid, "same-upload", data, "invoice.pdf", "application/pdf")
    assert first.status_code == second.status_code == 202
    assert first.json()["document"]["id"] == second.json()["document"]["id"]
    assert second.json()["operation"]["replayed"] is True
    old_hash = first.json()["document"]["sha256"]
    conflict = upload(
        client,
        token,
        pid,
        "same-upload",
        valid_image("PNG"),
        "invoice.png",
        "image/png",
    )
    assert conflict.status_code == 409
    with session_factory() as session:
        version = session.scalar(select(DocumentVersion))
        assert version.sha256_hex == old_hash
        assert session.scalar(select(func.count(DocumentVersion.id))) == 1
    with TestClient(create_app(settings)) as restarted:
        replay = upload(
            restarted, token, pid, "same-upload", data, "invoice.pdf", "application/pdf"
        )
        assert replay.status_code == 202
        assert replay.json()["operation"]["replayed"] is True


def test_concurrent_duplicate_upload_leaves_one_record_and_file(
    settings, client, created_procurement, session_factory
):
    token, _project, procurement = created_procurement
    pid = procurement["procurement"]["id"]
    data = valid_pdf()

    def submit():
        with TestClient(create_app(settings)) as thread_client:
            return upload(
                thread_client,
                token,
                pid,
                "concurrent-upload",
                data,
                "invoice.pdf",
                "application/pdf",
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit(), range(2)))
    assert [item.status_code for item in responses] == [202, 202]
    with session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 1
        assert session.scalar(select(func.count(DocumentVersion.id))) == 1
        assert session.scalar(
            select(func.count(Operation.id)).where(Operation.operation_kind == "document.upload")
        ) == 1
    assert len(list(settings.storage_root.rglob("*.bin"))) == 1
    assert list(settings.storage_root.rglob("*.upload")) == []


def test_request_body_limit_rejects_before_business_processing(
    client, created_procurement, settings
):
    token, _project, procurement = created_procurement
    response = upload(
        client,
        token,
        procurement["procurement"]["id"],
        "too-large",
        b"x" * (MAX_FILE_BYTES + 100_000),
        "invoice.pdf",
        "application/pdf",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_streamed_and_falsely_small_content_length_still_return_413(client, settings):
    boundary = "pog-limit-boundary"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="procurementId"\r\n\r\n'
        "00000000-0000-0000-0000-000000000001\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="category"\r\n\r\n'
        "invoice\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="large.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    body = prefix + b"x" * (MAX_FILE_BYTES + 100_000) + f"\r\n--{boundary}--\r\n".encode()
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Idempotency-Key": "body-limit",
    }
    streamed = client.post("/v2/documents", content=iter([body[:1024], body[1024:]]), headers=headers)
    assert streamed.status_code == 413
    assert streamed.json()["error"]["code"] == "request_too_large"
    forged = client.post(
        "/v2/documents",
        content=body,
        headers={**headers, "Content-Length": "1"},
    )
    assert forged.status_code == 413
    assert forged.json()["error"]["code"] == "request_too_large"
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_storage_failure_rolls_back_document_but_persists_failed_operation(
    monkeypatch, client, created_procurement, settings, session_factory
):
    token, _project, procurement = created_procurement

    def fail_commit(*_args, **_kwargs):
        raise APIError(500, "storage_commit_failed", "Evidence could not be stored")

    monkeypatch.setattr(PrivateFileStore, "commit", fail_commit)
    response = upload(
        client,
        token,
        procurement["procurement"]["id"],
        "storage-failure",
        valid_pdf(),
        "invoice.pdf",
        "application/pdf",
    )
    assert response.status_code == 500
    operation_id = response.json()["error"]["operationId"]
    with session_factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation.status == "failed"
        assert operation.error_code == "storage_commit_failed"
        assert session.scalar(select(func.count(Document.id))) == 0
        assert session.scalar(select(func.count(DocumentVersion.id))) == 0
    assert list(settings.storage_root.rglob("*.upload")) == []
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_database_commit_failure_removes_committed_file_and_rows(
    client, created_procurement, settings, session_factory
):
    token, _project, procurement = created_procurement

    def fail_after_evidence_flush(session):
        if any(isinstance(item, DocumentVersion) for item in session.identity_map.values()):
            raise RuntimeError("injected database commit failure")

    event.listen(Session, "before_commit", fail_after_evidence_flush)
    try:
        with TestClient(create_app(settings), raise_server_exceptions=False) as failing_client:
            response = upload(
                failing_client,
                token,
                procurement["procurement"]["id"],
                "db-commit-failure",
                valid_pdf(),
                "invoice.pdf",
                "application/pdf",
            )
        assert response.status_code == 500
    finally:
        event.remove(Session, "before_commit", fail_after_evidence_flush)
    with session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 0
        assert session.scalar(select(func.count(DocumentVersion.id))) == 0
        assert session.scalar(
            select(func.count(Operation.id)).where(
                Operation.idempotency_key == "db-commit-failure"
            )
        ) == 0
    assert list(settings.storage_root.rglob("*.upload")) == []
    assert list(settings.storage_root.rglob("*.bin")) == []


def test_commit_failure_after_link_and_name_collision_never_leave_or_overwrite_evidence(
    monkeypatch, tmp_path
):
    store = PrivateFileStore(tmp_path / "private")
    namespace = "00000000-0000-0000-0000-000000000001"
    staged = store.stage(BytesIO(valid_pdf()), "invoice.pdf", "application/pdf")
    monkeypatch.setattr("pog_api.file_store.secrets.token_hex", lambda _size: "a" * 64)
    target = store.root / namespace / (("a" * 64) + ".bin")
    target.parent.mkdir(mode=0o700)
    target.write_bytes(b"existing-evidence")

    original_chmod = os.chmod

    def fail_directory_chmod(path, mode):
        if Path(path) == target.parent:
            raise OSError("injected directory chmod failure")
        original_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", fail_directory_chmod)
    with pytest.raises(APIError, match="could not be stored"):
        store.commit(staged, namespace)
    assert target.read_bytes() == b"existing-evidence"
    monkeypatch.setattr(os, "chmod", original_chmod)

    with pytest.raises(APIError, match="already exists"):
        store.commit(staged, namespace)
    assert target.read_bytes() == b"existing-evidence"
    store.cleanup(staged.temp_path)

    staged = store.stage(BytesIO(valid_pdf()), "invoice.pdf", "application/pdf")
    target.unlink()

    def fail_file_chmod(path, mode):
        if Path(path).suffix == ".bin":
            raise OSError("injected chmod failure")
        original_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", fail_file_chmod)
    with pytest.raises(APIError, match="could not be stored"):
        store.commit(staged, namespace)
    assert not target.exists()
    assert list(store.root.rglob("*.bin")) == []
