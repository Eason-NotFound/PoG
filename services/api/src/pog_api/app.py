from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from .adapters import AdapterRegistry
from .amounts import decimal_to_uint_string
from .config import Settings
from .db import build_engine, build_session_factory, session_dependency
from .errors import APIError, install_error_handlers
from .file_store import PrivateFileStore
from .hashing import keccak256
from .idempotency import (
    audit,
    begin_operation,
    ensure_namespace,
    validate_idempotency_key,
    ensure_verified_namespace,
)
from .chain import LocalChainGateway
from .a2 import install_a2_routes
from .ids import new_resource_ids
from .models import (
    AuditLog,
    ChainTransaction,
    DeploymentInstance,
    Document,
    DocumentVersion,
    Operation,
    OperationStep,
    Procurement,
    Project,
    Role,
    SessionRecord,
    User,
    WalletAuthorization,
)
from .middleware import UploadBodyLimitMiddleware
from .schemas import (
    ChainState,
    DocumentResponse,
    DocumentMutationResponse,
    LoginRequest,
    OperationResponse,
    ProcurementCreate,
    ProcurementListResponse,
    ProcurementMutationResponse,
    ProcurementResponse,
    ProjectCreate,
    ProjectListResponse,
    ProjectMutationResponse,
    ProjectResponse,
    SessionResponse,
)
from .security import (
    Principal,
    expires_at,
    issue_session_token,
    session_token_hash,
    utcnow,
    verify_password,
)


FAIRNESS_RULE = (
    "All cumulative donations proportionally share confirmed project cost; "
    "after reconciliation, actual remaining stablecoins are returned proportionally "
    "to original Donors."
)
CLOSING_RULE = (
    "Closing blocks new donations/procurements/reservations. Existing obligations must "
    "be reconciled before original Donors can claim proportional stablecoin refunds; "
    "unresolved debt or any returned released funds remains blocked."
)
DOCUMENT_CATEGORIES = {
    "purchase_order",
    "request",
    "goods_request",
    "invoice",
    "goods_evidence",
    "receipt_evidence",
}
bearer = HTTPBearer(auto_error=False)


def _wallet_for_role(session: Session, user_id: UUID, role: str) -> WalletAuthorization:
    authorization = session.scalar(
        select(WalletAuthorization).where(
            WalletAuthorization.user_id == user_id,
            WalletAuthorization.role_name == role,
            WalletAuthorization.active.is_(True),
        )
    )
    if authorization is None:
        raise APIError(422, "role_wallet_not_found", f"User lacks active {role} wallet")
    return authorization


def _project_visible(project: Project, principal: Principal) -> bool:
    if principal.role == "donor":
        return True
    return principal.user_id in {
        project.foundation_user_id,
        project.recipient_user_id,
        project.human_approver_user_id,
    }


def _project_private(project: Project, principal: Principal) -> bool:
    return principal.user_id in {
        project.foundation_user_id,
        project.recipient_user_id,
        project.human_approver_user_id,
    }


def _project_response(project: Project, principal: Principal) -> ProjectResponse:
    private = _project_private(project, principal)
    return ProjectResponse(
        id=project.id,
        businessId=project.business_id,
        title=project.title,
        publicSummary=project.public_summary,
        fairnessRule=project.fairness_rule,
        closingRule=CLOSING_RULE,
        foundationWallet=project.foundation_wallet if private else None,
        recipientWallet=project.recipient_wallet if private else None,
        chainState=ChainState(
            status=project.chain_status,
            verified=project.chain_status in {"active", "closing", "refundable", "closed"}
            and project.chain_tx_hash is not None,
            transactionHash=project.chain_tx_hash,
            blockNumber=project.chain_block_number,
        ),
        createdAt=project.created_at,
    )


def _procurement_response(procurement: Procurement) -> ProcurementResponse:
    return ProcurementResponse(
        id=procurement.id,
        projectId=procurement.project_id,
        businessId=procurement.business_id,
        title=procurement.title,
        vendorWallet=procurement.vendor_wallet,
        budgetCapAtomic=decimal_to_uint_string(procurement.budget_cap_atomic),
        chainState=ChainState(
            status=procurement.chain_status,
            verified=procurement.chain_status in {
                "created", "po_recorded", "pre_assessed", "reserve_approval_pending", "reserved",
                "invoice_recorded", "receipt_confirmed", "final_assessed", "release_approval_pending",
                "funds_released", "settlement_recorded", "cancelled",
                "settlement_approval_pending", "payment_confirmed", "cancellation_approval_pending",
            }
            and procurement.chain_tx_hash is not None,
            transactionHash=procurement.chain_tx_hash,
            blockNumber=procurement.chain_block_number,
        ),
        createdAt=procurement.created_at,
    )


def _document_response(document: Document, version: DocumentVersion) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        versionId=version.id,
        procurementId=document.procurement_id,
        category=document.category,
        version=version.version,
        originalFilename=version.original_filename,
        contentType=version.content_type,
        sizeBytes=version.size_bytes,
        sha256=version.sha256_hex,
        keccak256=version.keccak256_hex,
        abiCombinationKeccak=version.abi_combination_keccak_hex,
        referenced=version.referenced,
        createdAt=version.created_at,
    )


def _operation_response(
    operation: Operation, replayed: bool, steps: list[dict[str, object]] | None = None,
) -> OperationResponse:
    chain_verified = operation.status == "confirmed" and bool(steps) and all(
        step.get("status") == "confirmed"
        and isinstance(step.get("transaction"), dict)
        and step["transaction"].get("status") == "confirmed"
        and step["transaction"].get("receiptStatus") == 1
        and step["transaction"].get("canonical") is True
        for step in steps
    )
    return OperationResponse(
        operationId=operation.id,
        status=operation.status,
        operationKind=operation.operation_kind,
        resourceType=operation.result_resource_type,
        resourceId=operation.result_resource_id,
        replayed=replayed,
        chainVerified=chain_verified,
        errorCode=operation.error_code,
        errorStatus=operation.error_status,
        errorMessage=operation.error_detail,
        steps=steps,
    )


def create_app(
    settings: Settings | None = None,
    adapters: AdapterRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    adapters = adapters or AdapterRegistry.a1_default()
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    file_store = PrivateFileStore(settings.storage_root)
    get_session = session_dependency(factory)
    gateway = None
    gateway_error = None
    if settings.chain_enabled:
        try:
            gateway = LocalChainGateway(
                settings.chain_manifest,
                Path(__file__).resolve().parents[4],
            )
        except Exception as exc:
            gateway_error = str(exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        file_store.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        yield
        engine.dispose()

    app = FastAPI(
        title="PoG API",
        version="0.2.0-a2-candidate",
        description=(
            "PoG A1 off-chain foundation with an opt-in, loopback-only A2 local-chain path. "
            "No chain transactions are available in the default mode. A2 adds restricted "
            "V2 operations and EIP-712 authorization through Recipient ReceiptConfirmed; "
            "real AI, conversion, payment, release and settlement remain unavailable. "
            "Login creates a new session on each success; logout is idempotent for the "
            "same bearer token."
        ),
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.add_middleware(UploadBodyLimitMiddleware)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.file_store = file_store
    app.state.adapters = adapters

    def current_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "authentication_required", "Bearer session token is required")
        token_hash = session_token_hash(credentials.credentials)
        with factory() as auth_session:
            row = auth_session.execute(
                select(SessionRecord, User)
                .join(User, User.id == SessionRecord.user_id)
                .where(SessionRecord.token_hash == token_hash)
            ).first()
        if row is None:
            raise APIError(401, "invalid_session", "Session token is invalid")
        session_record, user = row
        if (
            session_record.revoked_at is not None
            or session_record.expires_at <= utcnow()
            or not user.active
        ):
            raise APIError(401, "session_inactive", "Session is expired, revoked or inactive")
        with factory() as auth_session:
            wallets = auth_session.scalars(
                select(WalletAuthorization).where(
                    WalletAuthorization.user_id == user.id,
                    WalletAuthorization.active.is_(True),
                )
            ).all()
        if len(wallets) != 1:
            raise APIError(
                409, "ambiguous_role_wallet",
                "Session authorization requires exactly one active role-wallet",
            )
        wallet = wallets[0]
        return Principal(
            user_id=user.id,
            username=user.username,
            role=wallet.role_name,
            wallet_address=wallet.wallet_address,
            session_id=session_record.id,
        )

    def namespace(session: Session) -> DeploymentInstance:
        if gateway is not None:
            return ensure_verified_namespace(session, gateway)
        return ensure_namespace(session, settings.run_id, settings.instance_id)

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "healthy", "service": "pog-api", "version": "0.2.0-a2-candidate"}

    @app.get("/ready", tags=["system"])
    def ready(session: Session = Depends(get_session)):
        try:
            session.execute(text("SELECT 1"))
            revision = session.scalar(text("SELECT version_num FROM alembic_version"))
            required_tables = session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = current_schema() "
                    "AND table_name IN ('users','sessions','operations','projects',"
                    "'procurements','documents','document_versions','audit_logs',"
                    "'signing_requests','ledger_projections','donor_credit_projections')"
                )
            )
            storage_ready = file_store.root.is_dir() and os.access(file_store.root, os.W_OK)
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "database": "unavailable_or_unmigrated",
                    "storage": "unknown",
                    "deployment": {
                        "mode": "a1_mock_unverified",
                        "verified": False,
                        "chainVerified": False,
                    },
                    "adapters": adapters.statuses(),
                },
            )
        expected_revision = "c31003a20003"
        database_ready = revision == expected_revision and required_tables == 11
        chain_ready = not settings.chain_enabled or gateway is not None
        if not database_ready or not storage_ready or not chain_ready:
            return JSONResponse(
                status_code=503,
                content={
                    "ready": False,
                    "database": "ready" if database_ready else "unmigrated",
                    "storage": "ready" if storage_ready else "unavailable",
                    "deployment": {
                        "mode": "a1_mock_unverified",
                        "verified": False,
                        "chainVerified": False,
                    },
                    "adapters": adapters.statuses(),
                    "chainError": gateway_error,
                },
            )
        return {
            "ready": True,
            "database": "ready",
            "storage": "ready" if storage_ready else "unavailable",
            "deployment": {
                "mode": "a1_mock_unverified",
                "verified": False,
                "chainVerified": False,
            },
            "adapters": adapters.statuses(),
            "chainGate": "verified" if gateway is not None else "disabled",
        }

    @app.get("/v2/deployment-config", tags=["system"])
    def deployment_config(session: Session = Depends(get_session)):
        with session.begin():
            item = namespace(session)
        if gateway is not None:
            return {
                "schemaVersion": item.schema_version, "runId": item.run_id,
                "instanceId": item.instance_id, "mode": "a2_local_verified",
                "verified": True, "chainId": item.chain_id, "rpcUrl": item.rpc_url,
                "contracts": {
                    name: {"address": gateway.contract_address(name)}
                    for name in ("MockHKD", "PoGRegistryV2", "ProcurementEscrowV2")
                },
                "adapters": adapters.statuses(),
                "warning": "Local unlocked Anvil only; MockHKD has no value; AI/payment unavailable.",
            }
        return {
            "schemaVersion": item.schema_version,
            "runId": item.run_id,
            "instanceId": item.instance_id,
            "mode": "a1_mock_unverified",
            "verified": False,
            "chainId": None,
            "rpcUrl": None,
            "contracts": {},
            "adapters": adapters.statuses(),
            "warning": "A1 mock namespace only; no RPC or deployment verification performed.",
        }

    @app.post("/v2/sessions", response_model=SessionResponse, tags=["sessions"])
    def login(body: LoginRequest, session: Session = Depends(get_session)):
        denied = False
        with session.begin():
            user = session.scalar(select(User).where(User.username == body.username))
            if user is None or not user.active or not verify_password(body.password, user.password_hash):
                username_hash = hashlib.sha256(body.username.encode("utf-8")).hexdigest()
                audit(
                    session,
                    principal_id=None,
                    action="session.login",
                    outcome="denied",
                    metadata={"usernameSha256": username_hash},
                )
                denied = True
            else:
                wallets = session.scalars(
                    select(WalletAuthorization).where(
                        WalletAuthorization.user_id == user.id,
                        WalletAuthorization.active.is_(True),
                    )
                ).all()
                if len(wallets) != 1:
                    raise APIError(409, "ambiguous_role_wallet", "Demo user must have one active role-wallet")
                wallet = wallets[0]
                ttl = body.expires_in_seconds or settings.session_ttl_seconds
                token, token_hash = issue_session_token()
                expiry = expires_at(ttl)
                record = SessionRecord(user_id=user.id, token_hash=token_hash, expires_at=expiry)
                session.add(record)
                session.flush()
                audit(
                    session,
                    principal_id=user.id,
                    action="session.login",
                    outcome="succeeded",
                    resource_type="session",
                    resource_id=record.id,
                )
        if denied:
            raise APIError(401, "invalid_credentials", "Username or password is incorrect")
        return SessionResponse(
            token=token,
            expiresAt=expiry,
            user={
                "id": str(user.id),
                "username": user.username,
                "displayName": user.display_name,
                "role": wallet.role_name,
                "walletAddress": wallet.wallet_address,
            },
        )

    @app.get("/v2/me", tags=["sessions"])
    def me(principal: Principal = Depends(current_principal)):
        return {
            "id": str(principal.user_id),
            "username": principal.username,
            "role": principal.role,
            "walletAddress": principal.wallet_address,
        }

    @app.delete("/v2/sessions/current", tags=["sessions"])
    def logout(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        session: Session = Depends(get_session),
    ):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise APIError(401, "authentication_required", "Bearer session token is required")
        digest = session_token_hash(credentials.credentials)
        with session.begin():
            record = session.scalar(select(SessionRecord).where(SessionRecord.token_hash == digest))
            if record is None:
                raise APIError(401, "invalid_session", "Session token is invalid")
            already = record.revoked_at is not None
            if not already:
                record.revoked_at = utcnow()
                audit(
                    session,
                    principal_id=record.user_id,
                    action="session.logout",
                    outcome="succeeded",
                    resource_type="session",
                    resource_id=record.id,
                )
        return {"loggedOut": True, "alreadyLoggedOut": already}

    @app.post(
        "/v2/projects",
        status_code=202,
        response_model=ProjectMutationResponse,
        tags=["projects"],
        responses={409: {"description": "Idempotency or state conflict"}},
    )
    def create_project(
        body: ProjectCreate,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        if principal.role != "foundation":
            raise APIError(403, "role_forbidden", "Only Foundation may create project drafts")
        key = validate_idempotency_key(idempotency_key)
        failure: APIError | None = None
        project: Project | None = None
        with session.begin():
            ns = namespace(session)
            payload = body.model_dump(mode="json", by_alias=True)
            operation, replayed = begin_operation(
                session,
                namespace_id=ns.id,
                principal_id=principal.user_id,
                operation_kind="project.create_draft",
                idempotency_key=key,
                validated_payload=payload,
            )
            if replayed:
                if operation.status == "failed":
                    failure = APIError(
                        operation.error_status or 422,
                        operation.error_code or "operation_failed",
                        operation.error_detail or "Original operation failed",
                        operation_id=str(operation.id),
                    )
                elif operation.result_resource_id is None:
                    failure = APIError(
                        409,
                        "operation_incomplete",
                        "Original operation has no resource result",
                        operation_id=str(operation.id),
                    )
                else:
                    project = session.get(Project, operation.result_resource_id)
            else:
                try:
                    recipient_wallet = _wallet_for_role(
                        session, body.recipient_user_id, "recipient"
                    )
                    human_wallet = _wallet_for_role(
                        session, body.human_approver_user_id, "human_approver"
                    )
                    project_id, business_id = new_resource_ids(
                        ns.run_id, ns.instance_id, "project"
                    )
                    project = Project(
                        id=project_id,
                        namespace_id=ns.id,
                        business_id=business_id,
                        title=body.title,
                        public_summary=body.public_summary,
                        foundation_user_id=principal.user_id,
                        recipient_user_id=body.recipient_user_id,
                        human_approver_user_id=body.human_approver_user_id,
                        foundation_wallet=principal.wallet_address,
                        recipient_wallet=recipient_wallet.wallet_address,
                        asset_symbol="mHKD",
                        fairness_rule=FAIRNESS_RULE,
                        chain_status="off_chain_draft",
                    )
                    session.add(project)
                    session.flush()
                    operation.result_resource_type = "project"
                    operation.result_resource_id = project.id
                    audit(
                        session,
                        principal_id=principal.user_id,
                        operation_id=operation.id,
                        action="project.create_draft",
                        outcome="awaiting_authorization",
                        resource_type="project",
                        resource_id=project.id,
                        metadata={
                            "chainVerified": False,
                            "humanApproverWallet": human_wallet.wallet_address,
                        },
                    )
                except APIError as exc:
                    operation.status = "failed"
                    operation.error_code = exc.code
                    operation.error_status = exc.status_code
                    operation.error_detail = exc.message
                    audit(
                        session,
                        principal_id=principal.user_id,
                        operation_id=operation.id,
                        action="project.create_draft",
                        outcome="failed",
                        metadata={"errorCode": exc.code},
                    )
                    failure = APIError(
                        exc.status_code,
                        exc.code,
                        exc.message,
                        operation_id=str(operation.id),
                    )
        if failure is not None:
            raise failure
        if project is None:
            raise APIError(500, "operation_result_missing", "Project result is missing")
        return {
            "operation": _operation_response(operation, replayed),
            "project": _project_response(project, principal),
        }

    @app.get("/v2/projects", response_model=ProjectListResponse, tags=["projects"])
    def list_projects(
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        ns = namespace(session)
        statement = select(Project).where(Project.namespace_id == ns.id)
        if principal.role == "foundation":
            statement = statement.where(Project.foundation_user_id == principal.user_id)
        elif principal.role == "recipient":
            statement = statement.where(Project.recipient_user_id == principal.user_id)
        elif principal.role == "human_approver":
            statement = statement.where(Project.human_approver_user_id == principal.user_id)
        elif principal.role != "donor":
            raise APIError(403, "role_forbidden", "Role cannot list projects")
        projects = session.scalars(statement.order_by(Project.created_at)).all()
        return {"items": [_project_response(item, principal) for item in projects]}

    @app.get("/v2/projects/{project_id}", response_model=ProjectResponse, tags=["projects"])
    def get_project(
        project_id: UUID,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        ns = namespace(session)
        project = session.get(Project, project_id)
        if project is None or project.namespace_id != ns.id:
            raise APIError(404, "project_not_found", "Project not found")
        if not _project_visible(project, principal):
            raise APIError(403, "project_forbidden", "Project is outside principal scope")
        return _project_response(project, principal)

    @app.post(
        "/v2/procurements",
        status_code=202,
        response_model=ProcurementMutationResponse,
        tags=["procurements"],
        responses={409: {"description": "Idempotency or state conflict"}},
    )
    def create_procurement(
        body: ProcurementCreate,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        if principal.role != "foundation":
            raise APIError(403, "role_forbidden", "Only Foundation may create procurement drafts")
        key = validate_idempotency_key(idempotency_key)
        failure: APIError | None = None
        procurement: Procurement | None = None
        with session.begin():
            ns = namespace(session)
            payload = body.model_dump(mode="json", by_alias=True)
            operation, replayed = begin_operation(
                session,
                namespace_id=ns.id,
                principal_id=principal.user_id,
                operation_kind="procurement.create_draft",
                idempotency_key=key,
                validated_payload=payload,
            )
            if replayed:
                if operation.status == "failed":
                    failure = APIError(
                        operation.error_status or 422,
                        operation.error_code or "operation_failed",
                        operation.error_detail or "Original operation failed",
                        operation_id=str(operation.id),
                    )
                else:
                    procurement = session.get(Procurement, operation.result_resource_id)
            else:
                try:
                    project = session.get(Project, body.project_id)
                    if project is None or project.namespace_id != ns.id:
                        raise APIError(404, "project_not_found", "Project not found")
                    if project.foundation_user_id != principal.user_id:
                        raise APIError(403, "project_forbidden", "Foundation does not own project")
                    procurement_id, business_id = new_resource_ids(
                        ns.run_id, ns.instance_id, "procurement"
                    )
                    procurement = Procurement(
                        id=procurement_id,
                        namespace_id=ns.id,
                        project_id=project.id,
                        business_id=business_id,
                        title=body.title,
                        foundation_user_id=principal.user_id,
                        vendor_wallet=body.vendor_wallet,
                        budget_cap_atomic=Decimal(body.budget_cap_atomic),
                        chain_status="off_chain_draft",
                    )
                    session.add(procurement)
                    session.flush()
                    operation.result_resource_type = "procurement"
                    operation.result_resource_id = procurement.id
                    audit(
                        session,
                        principal_id=principal.user_id,
                        operation_id=operation.id,
                        action="procurement.create_draft",
                        outcome="awaiting_authorization",
                        resource_type="procurement",
                        resource_id=procurement.id,
                        metadata={"chainVerified": False},
                    )
                except APIError as exc:
                    operation.status = "failed"
                    operation.error_code = exc.code
                    operation.error_status = exc.status_code
                    operation.error_detail = exc.message
                    audit(
                        session,
                        principal_id=principal.user_id,
                        operation_id=operation.id,
                        action="procurement.create_draft",
                        outcome="failed",
                        metadata={"errorCode": exc.code},
                    )
                    failure = APIError(
                        exc.status_code,
                        exc.code,
                        exc.message,
                        operation_id=str(operation.id),
                    )
        if failure is not None:
            raise failure
        if procurement is None:
            raise APIError(500, "operation_result_missing", "Procurement result is missing")
        return {
            "operation": _operation_response(operation, replayed),
            "procurement": _procurement_response(procurement),
        }

    @app.get(
        "/v2/procurements", response_model=ProcurementListResponse, tags=["procurements"]
    )
    def list_procurements(
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        ns = namespace(session)
        statement = (
            select(Procurement)
            .join(Project, Project.id == Procurement.project_id)
            .where(Procurement.namespace_id == ns.id)
        )
        if principal.role == "foundation":
            statement = statement.where(Project.foundation_user_id == principal.user_id)
        elif principal.role == "recipient":
            statement = statement.where(Project.recipient_user_id == principal.user_id)
        elif principal.role == "human_approver":
            statement = statement.where(Project.human_approver_user_id == principal.user_id)
        else:
            raise APIError(403, "role_forbidden", "Role cannot list private procurements")
        items = session.scalars(statement.order_by(Procurement.created_at)).all()
        return {"items": [_procurement_response(item) for item in items]}

    @app.get(
        "/v2/procurements/{procurement_id}",
        response_model=ProcurementResponse,
        tags=["procurements"],
    )
    def get_procurement(
        procurement_id: UUID,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        ns = namespace(session)
        procurement = session.get(Procurement, procurement_id)
        if procurement is None or procurement.namespace_id != ns.id:
            raise APIError(404, "procurement_not_found", "Procurement not found")
        project = session.get(Project, procurement.project_id)
        if project is None or not _project_private(project, principal):
            raise APIError(403, "procurement_forbidden", "Procurement is outside principal scope")
        return _procurement_response(procurement)

    @app.post(
        "/v2/documents",
        status_code=202,
        response_model=DocumentMutationResponse,
        tags=["documents"],
        responses={
            409: {"description": "Idempotency conflict"},
            413: {"description": "Request body too large"},
        },
    )
    def upload_document(
        procurement_id: Annotated[UUID, Form(alias="procurementId")],
        category: Annotated[str, Form(min_length=1, max_length=40)],
        file: Annotated[UploadFile, File()],
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ):
        key = validate_idempotency_key(idempotency_key)
        if category not in DOCUMENT_CATEGORIES:
            raise APIError(422, "invalid_document_category", "Unsupported document category")
        if principal.role not in {"foundation", "recipient"}:
            raise APIError(403, "role_forbidden", "Role cannot upload evidence")
        if principal.role == "recipient" and category != "receipt_evidence":
            raise APIError(403, "document_category_forbidden", "Recipient may upload receipt evidence only")
        staged = file_store.stage(file.file, file.filename or "", file.content_type or "")
        committed_path: Path | None = None
        failure: APIError | None = None
        document: Document | None = None
        version: DocumentVersion | None = None
        payload = {
            "procurementId": str(procurement_id),
            "category": category,
            "filename": staged.original_filename,
            "contentType": staged.content_type,
            "sizeBytes": staged.size_bytes,
            "sha256": staged.sha256_hex,
            "keccak256": staged.keccak256_hex,
        }
        try:
            with session.begin():
                ns = namespace(session)
                operation, replayed = begin_operation(
                    session,
                    namespace_id=ns.id,
                    principal_id=principal.user_id,
                    operation_kind="document.upload",
                    idempotency_key=key,
                    validated_payload=payload,
                )
                if replayed:
                    if operation.status == "failed":
                        failure = APIError(
                            operation.error_status or 422,
                            operation.error_code or "operation_failed",
                            operation.error_detail or "Original upload failed",
                            operation_id=str(operation.id),
                        )
                    else:
                        version = session.get(DocumentVersion, operation.result_resource_id)
                        if version is None:
                            failure = APIError(
                                409,
                                "operation_incomplete",
                                "Original upload has no document result",
                                operation_id=str(operation.id),
                            )
                        else:
                            document = session.get(Document, version.document_id)
                else:
                    business = session.begin_nested()
                    try:
                        procurement = session.get(Procurement, procurement_id)
                        if procurement is None or procurement.namespace_id != ns.id:
                            raise APIError(404, "procurement_not_found", "Procurement not found")
                        project = session.get(Project, procurement.project_id)
                        allowed = (
                            principal.user_id == project.foundation_user_id
                            or principal.user_id == project.recipient_user_id
                        )
                        if not allowed:
                            raise APIError(
                                403,
                                "document_forbidden",
                                "Procurement is outside principal scope",
                            )
                        document = Document(
                            namespace_id=ns.id,
                            procurement_id=procurement.id,
                            category=category,
                            owner_user_id=principal.user_id,
                        )
                        session.add(document)
                        session.flush()
                        storage_key, committed_path = file_store.commit(staged, str(ns.id))
                        version = DocumentVersion(
                            document_id=document.id,
                            version=1,
                            original_filename=staged.original_filename,
                            content_type=staged.content_type,
                            size_bytes=staged.size_bytes,
                            sha256_hex=staged.sha256_hex,
                            keccak256_hex=staged.keccak256_hex,
                            abi_combination_keccak_hex=None,
                            storage_key=storage_key,
                            uploaded_by_user_id=principal.user_id,
                            referenced=False,
                        )
                        session.add(version)
                        session.flush()
                        operation.result_resource_type = "document_version"
                        operation.result_resource_id = version.id
                        audit(
                            session,
                            principal_id=principal.user_id,
                            operation_id=operation.id,
                            action="document.upload",
                            outcome="awaiting_authorization",
                            resource_type="document",
                            resource_id=document.id,
                            metadata={
                                "sha256": staged.sha256_hex,
                                "keccak256": staged.keccak256_hex,
                                "private": True,
                            },
                        )
                        business.commit()
                    except APIError as exc:
                        business.rollback()
                        operation.status = "failed"
                        operation.error_code = exc.code
                        operation.error_status = exc.status_code
                        operation.error_detail = exc.message
                        audit(
                            session,
                            principal_id=principal.user_id,
                            operation_id=operation.id,
                            action="document.upload",
                            outcome="failed",
                            metadata={"errorCode": exc.code},
                        )
                        failure = APIError(
                            exc.status_code,
                            exc.code,
                            exc.message,
                            operation_id=str(operation.id),
                        )
        except Exception:
            file_store.cleanup(staged.temp_path)
            file_store.cleanup(committed_path)
            raise
        if replayed or failure is not None:
            file_store.cleanup(staged.temp_path)
        if failure is not None:
            raise failure
        if document is None or version is None:
            raise APIError(500, "operation_result_missing", "Document result is missing")
        return {
            "operation": _operation_response(operation, replayed),
            "document": _document_response(document, version),
        }

    def _authorized_document(
        session: Session, document_id: UUID, principal: Principal
    ) -> tuple[Document, DocumentVersion]:
        ns = namespace(session)
        document = session.get(Document, document_id)
        if document is None or document.namespace_id != ns.id:
            raise APIError(404, "document_not_found", "Document not found")
        procurement = session.get(Procurement, document.procurement_id)
        project = session.get(Project, procurement.project_id)
        if not _project_private(project, principal):
            raise APIError(403, "document_forbidden", "Private evidence is outside principal scope")
        version = session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version.desc())
            .limit(1)
        )
        if version is None:
            raise APIError(404, "document_version_not_found", "Document version not found")
        return document, version

    @app.get(
        "/v2/documents/{document_id}", response_model=DocumentResponse, tags=["documents"]
    )
    def get_document(
        document_id: UUID,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        document, version = _authorized_document(session, document_id, principal)
        return _document_response(document, version)

    @app.get("/v2/documents/{document_id}/content", tags=["documents"])
    def get_document_content(
        document_id: UUID,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        document, version = _authorized_document(session, document_id, principal)
        path = file_store.resolve(version.storage_key)
        return FileResponse(
            path,
            media_type=version.content_type,
            filename=version.original_filename,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get(
        "/v2/operations/{operation_id}",
        response_model=OperationResponse,
        tags=["operations"],
    )
    def get_operation(
        operation_id: UUID,
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ):
        ns = namespace(session)
        operation = session.get(Operation, operation_id)
        if operation is None or operation.namespace_id != ns.id:
            raise APIError(404, "operation_not_found", "Operation not found")
        if operation.principal_id != principal.user_id:
            raise APIError(403, "operation_forbidden", "Operation belongs to another principal")
        step_rows = session.scalars(
            select(OperationStep).where(OperationStep.operation_id == operation.id)
            .order_by(OperationStep.step_index)
        ).all()
        step_facts: list[dict[str, object]] = []
        for step in step_rows:
            transaction = session.scalar(
                select(ChainTransaction).where(ChainTransaction.step_id == step.id)
            )
            tx_fact = None
            if transaction is not None:
                receipt = transaction.receipt_json or {}
                tx_fact = {
                    "status": transaction.status,
                    "transactionHash": transaction.tx_hash,
                    "receiptStatus": receipt.get("status"),
                    "blockNumber": receipt.get("blockNumber"),
                    "blockHash": transaction.block_hash,
                    "canonical": transaction.canonical,
                    "confirmedAt": transaction.confirmed_at,
                }
            step_facts.append({
                "stepIndex": step.step_index,
                "kind": step.kind,
                "status": step.status,
                "action": step.detail.get("action"),
                "expectedEvent": step.detail.get("expectedEvent"),
                "transaction": tx_fact,
            })
        return _operation_response(operation, False, step_facts or None)

    install_a2_routes(
        app, get_session=get_session, current_principal=current_principal,
        namespace=namespace, gateway=gateway, gateway_error=gateway_error,
        settings=settings,
    )
    return app


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "pog_api.app:create_app",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )
