from __future__ import annotations

from uuid import UUID, uuid4

from .hashing import keccak256


def new_resource_ids(run_id: str, instance_id: str, resource_type: str) -> tuple[UUID, str]:
    local_id = uuid4()
    domain = f"POG_A1:{run_id}:{instance_id}:{resource_type}:{local_id}".encode("utf-8")
    return local_id, "0x" + keccak256(domain)
