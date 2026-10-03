from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictStr, field_validator, model_serializer

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
    status: str
    verified: bool
    transaction_hash: str | None = Field(default=None, alias="transactionHash")
    block_number: int | None = Field(default=None, alias="blockNumber")

    @model_serializer(mode="wrap")
    def omit_unavailable_facts(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


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


class ChainTransactionFact(StrictModel):
    status: str
    transaction_hash: str | None = Field(default=None, alias="transactionHash")
    receipt_status: int | None = Field(default=None, alias="receiptStatus")
    block_number: int | None = Field(default=None, alias="blockNumber")
    block_hash: str | None = Field(default=None, alias="blockHash")
    canonical: bool
    confirmed_at: datetime | None = Field(default=None, alias="confirmedAt")

    @model_serializer(mode="wrap")
    def omit_unavailable_facts(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class OperationStepFact(StrictModel):
    step_index: int = Field(alias="stepIndex")
    kind: str
    status: str
    action: str | None = None
    expected_event: str | None = Field(default=None, alias="expectedEvent")
    transaction: ChainTransactionFact | None = None

    @model_serializer(mode="wrap")
    def omit_unavailable_facts(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class OperationResponse(StrictModel):
    operation_id: UUID = Field(alias="operationId")
    status: str
    operation_kind: str = Field(alias="operationKind")
    resource_type: str | None = Field(alias="resourceType")
    resource_id: UUID | None = Field(alias="resourceId")
    replayed: bool
    chain_verified: bool = Field(alias="chainVerified")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_status: int | None = Field(default=None, alias="errorStatus")
    error_message: str | None = Field(default=None, alias="errorMessage")
    steps: list[OperationStepFact] | None = None

    @model_serializer(mode="wrap")
    def omit_unavailable_facts(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class DocumentResponse(StrictModel):
    id: UUID
    version_id: UUID = Field(alias="versionId")
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


class EmptyMutation(StrictModel):
    pass


class DonationCreate(StrictModel):
    amount_atomic: UInt256String = Field(alias="amountAtomic")


class PurchaseOrderCreate(StrictModel):
    po_document_version_id: UUID = Field(alias="poDocumentVersionId")
    request_document_version_id: UUID = Field(alias="requestDocumentVersionId")
    goods_request_document_version_id: UUID = Field(alias="goodsRequestDocumentVersionId")


class InvoiceAndGoodsCreate(StrictModel):
    invoice_document_version_id: UUID = Field(alias="invoiceDocumentVersionId")
    goods_document_version_id: UUID = Field(alias="goodsDocumentVersionId")
    invoice_amount_atomic: UInt256String = Field(alias="invoiceAmountAtomic")


class SigningRequestCreate(StrictModel):
    kind: Literal["ai_pre", "reserve", "receipt"]
    reserve_amount_atomic: UInt256String | None = Field(default=None, alias="reserveAmountAtomic")
    receipt_evidence_document_version_id: UUID | None = Field(
        default=None, alias="receiptEvidenceDocumentVersionId"
    )
    deadline_ttl_seconds: int | None = Field(default=None, alias="deadlineTtlSeconds", ge=60, le=3600)


class DemoSignRequest(StrictModel):
    confirm: Literal[True]


class SignatureSubmit(StrictModel):
    signature: SafeStrictStr = Field(min_length=132, max_length=132)


class ReserveCreate(StrictModel):
    reserve_amount_atomic: UInt256String = Field(alias="reserveAmountAtomic")
