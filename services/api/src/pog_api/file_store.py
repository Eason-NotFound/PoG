from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .errors import APIError
from .hashing import new_keccak256


MAX_FILE_BYTES = 10 * 1024 * 1024
_ALLOWED = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
}


@dataclass(frozen=True)
class StagedFile:
    temp_path: Path
    original_filename: str
    content_type: str
    size_bytes: int
    sha256_hex: str
    keccak256_hex: str


class PrivateFileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.temp_root = self.root / ".tmp"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.temp_root, 0o700)

    def _safe_filename(self, filename: str) -> str:
        try:
            filename.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise APIError(400, "invalid_filename", "Filename must contain valid Unicode") from exc
        if (
            not filename
            or len(filename) > 255
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
            or Path(filename).name != filename
        ):
            raise APIError(400, "invalid_filename", "Filename must be a simple basename")
        return filename

    def stage(self, stream: BinaryIO, filename: str, declared_content_type: str) -> StagedFile:
        filename = self._safe_filename(filename)
        suffix = Path(filename).suffix.lower()
        if declared_content_type not in _ALLOWED:
            raise APIError(422, "unsupported_file_type", "Only PDF, JPEG and PNG are accepted")
        if suffix not in _ALLOWED[declared_content_type]:
            raise APIError(422, "file_extension_mismatch", "Filename extension does not match MIME")
        temp_path = self.temp_root / (secrets.token_hex(24) + ".upload")
        sha = hashlib.sha256()
        keccak = new_keccak256()
        size = 0
        head = bytearray()
        tail = bytearray()
        try:
            with temp_path.open("xb") as output:
                os.chmod(temp_path, 0o600)
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise APIError(422, "file_too_large", "File exceeds the 10 MiB limit")
                    if len(head) < 16:
                        head.extend(chunk[: 16 - len(head)])
                    tail.extend(chunk)
                    if len(tail) > 16:
                        del tail[:-16]
                    sha.update(chunk)
                    keccak.update(chunk)
                    output.write(chunk)
            if size == 0:
                raise APIError(422, "empty_file", "Empty files are not accepted")
            detected = self._detect(bytes(head), bytes(tail))
            if detected != declared_content_type:
                raise APIError(
                    422,
                    "file_content_mismatch",
                    "Declared MIME does not match the file signature",
                )
            self._validate_structure(temp_path, detected)
            return StagedFile(
                temp_path=temp_path,
                original_filename=filename,
                content_type=declared_content_type,
                size_bytes=size,
                sha256_hex=sha.hexdigest(),
                keccak256_hex=keccak.hexdigest(),
            )
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _detect(self, head: bytes, tail: bytes) -> str | None:
        if head.startswith(b"%PDF-"):
            return "application/pdf"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if head.startswith(b"\xff\xd8\xff") and tail.endswith(b"\xff\xd9"):
            return "image/jpeg"
        return None

    def _validate_structure(self, path: Path, content_type: str) -> None:
        try:
            if content_type == "application/pdf":
                reader = PdfReader(path, strict=True)
                if len(reader.pages) < 1:
                    raise ValueError("PDF has no pages")
            else:
                with Image.open(path) as image:
                    expected = "JPEG" if content_type == "image/jpeg" else "PNG"
                    if image.format != expected:
                        raise ValueError("image format mismatch")
                    image.verify()
        except (OSError, ValueError, UnidentifiedImageError, EOFError, PdfReadError) as exc:
            raise APIError(
                422,
                "invalid_file_content",
                "File is truncated or structurally invalid",
            ) from exc

    def commit(self, staged: StagedFile, namespace_id: str) -> tuple[str, Path]:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", namespace_id):
            raise APIError(500, "invalid_storage_namespace", "Invalid storage namespace")
        directory = (self.root / namespace_id).resolve()
        if self.root not in directory.parents:
            raise APIError(500, "storage_path_error", "Storage namespace escaped root")
        filename = secrets.token_hex(32) + ".bin"
        target = directory / filename
        created_target = False
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
            # Hard-linking is atomic and exclusive: unlike os.replace(), it can never
            # overwrite an earlier evidence object if a generated name collides.
            os.link(staged.temp_path, target)
            created_target = True
            staged.temp_path.unlink()
            os.chmod(target, 0o600)
        except FileExistsError as exc:
            raise APIError(
                500,
                "storage_key_collision",
                "Generated evidence storage key already exists",
            ) from exc
        except OSError as exc:
            if created_target:
                target.unlink(missing_ok=True)
            raise APIError(500, "storage_commit_failed", "Evidence could not be stored") from exc
        return f"{namespace_id}/{filename}", target

    def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if self.root not in candidate.parents:
            raise APIError(404, "document_not_found", "Document content not found")
        if not candidate.is_file():
            raise APIError(404, "document_not_found", "Document content not found")
        return candidate

    def cleanup(self, path: Path | None) -> None:
        if path is not None:
            path.unlink(missing_ok=True)
