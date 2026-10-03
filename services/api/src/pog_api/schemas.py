from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictStr, field_validator

from .amounts import UInt256String


_WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def validate_unicode(value: str) -> str:
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError("must contain valid Unicode without isolated surrogates") from exc
    return value


SafeStrictStr = Annotated[StrictStr, AfterValidator(validate_unicode)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LoginRequest(StrictModel):
    username: SafeStrictStr = Field(min_length=1, max_length=80)
    password: SafeStrictStr = Field(min_length=1, max_length=1024)
    expires_in_seconds: int | None = Field(default=None, alias="expiresInSeconds", ge=60, le=86400)


class SessionResponse(StrictModel):
    token: str
    expires_at: datetime = Field(alias="expiresAt")
    user: dict[str, object]


class ProjectCreate(StrictModel):
    title: SafeStrictStr = Field(min_length=1, max_length=160)
    public_summary: SafeStrictStr = Field(alias="publicSummary", min_length=1, max_length=4000)
    recipient_user_id: UUID = Field(alias="recipientUserId")
    human_approver_user_id: UUID = Field(alias="humanApproverUserId")


class ProcurementCreate(StrictModel):
    project_id: UUID = Field(alias="projectId")
    title: SafeStrictStr = Field(min_length=1, max_length=160)
    vendor_wallet: SafeStrictStr = Field(alias="vendorWallet")
    budget_cap_atomic: UInt256String = Field(alias="budgetCapAtomic")

    @field_validator("vendor_wallet")
    @classmethod
    def validate_wallet(cls, value: str) -> str:
        if not _WALLET_RE.fullmatch(value):
            raise ValueError("must be a 20-byte 0x-prefixed wallet address")
        return value


class ChainState(StrictModel):
    status: Literal["off_chain_draft"]
    verified: Literal[False]


class ProjectResponse(StrictModel):
    id: UUID
    business_id: str = Field(alias="businessId")
    title: str
    public_summary: str = Field(alias="publicSummary")
    fairness_rule: str = Field(alias="fairnessRule")
    closing_rule: str = Field(alias="closingRule")
    foundation_wallet: str | None = Field(default=None, alias="foundationWallet")
    recipient_wallet: str | None = Field(default=None, alias="recipientWallet")
    chain_state: ChainState = Field(alias="chainState")
    created_at: datetime = Field(alias="createdAt")


class ProcurementResponse(StrictModel):
    id: UUID
    project_id: UUID = Field(alias="projectId")
    business_id: str = Field(alias="businessId")
    title: str
    vendor_wallet: str = Field(alias="vendorWallet")
    budget_cap_atomic: str = Field(alias="budgetCapAtomic")
    chain_state: ChainState = Field(alias="chainState")
    created_at: datetime = Field(alias="createdAt")


class OperationResponse(StrictModel):
    operation_id: UUID = Field(alias="operationId")
    status: str
    operation_kind: str = Field(alias="operationKind")
    resource_type: str | None = Field(alias="resourceType")
    resource_id: UUID | None = Field(alias="resourceId")
    replayed: bool
    chain_verified: Literal[False] = Field(alias="chainVerified")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_status: int | None = Field(default=None, alias="errorStatus")
    error_message: str | None = Field(default=None, alias="errorMessage")


class DocumentResponse(StrictModel):
    id: UUID
    procurement_id: UUID = Field(alias="procurementId")
    category: str
    version: int
    original_filename: str = Field(alias="originalFilename")
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes")
    sha256: str
    keccak256: str
    abi_combination_keccak: str | None = Field(alias="abiCombinationKeccak")
    referenced: bool
    created_at: datetime = Field(alias="createdAt")


class ProjectMutationResponse(StrictModel):
    operation: OperationResponse
    project: ProjectResponse


class ProcurementMutationResponse(StrictModel):
    operation: OperationResponse
    procurement: ProcurementResponse


class DocumentMutationResponse(StrictModel):
    operation: OperationResponse
    document: DocumentResponse


class ProjectListResponse(StrictModel):
    items: list[ProjectResponse]


class ProcurementListResponse(StrictModel):
    items: list[ProcurementResponse]
