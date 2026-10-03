from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_root: Path
    session_ttl_seconds: int = 3600
    run_id: str = "a1-local-run"
    instance_id: str = "a1-local-instance"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    deployment_verified: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("POG_DATABASE_URL", "")
        storage_root = Path(os.getenv("POG_STORAGE_ROOT", ".local/api-storage")).resolve()
        ttl = int(os.getenv("POG_SESSION_TTL_SECONDS", "3600"))
        port = int(os.getenv("POG_BIND_PORT", "8080"))
        host = os.getenv("POG_BIND_HOST", "127.0.0.1")
        if not database_url:
            raise RuntimeError("POG_DATABASE_URL is required")
        if host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("A1 only permits loopback bind hosts")
        if ttl < 60 or ttl > 86400:
            raise RuntimeError("POG_SESSION_TTL_SECONDS must be between 60 and 86400")
        if port < 1024 or port > 65535:
            raise RuntimeError("POG_BIND_PORT must be between 1024 and 65535")
        return cls(
            database_url=database_url,
            storage_root=storage_root,
            session_ttl_seconds=ttl,
            run_id=os.getenv("POG_A1_RUN_ID", "a1-local-run"),
            instance_id=os.getenv("POG_A1_INSTANCE_ID", "a1-local-instance"),
            bind_host=host,
            bind_port=port,
            deployment_verified=False,
        )
