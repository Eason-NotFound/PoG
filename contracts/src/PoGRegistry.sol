// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { Ownable2Step } from "@openzeppelin/contracts/access/Ownable2Step.sol";
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import { Pausable } from "@openzeppelin/contracts/utils/Pausable.sol";
import { ReentrancyGuard } from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import { EIP712 } from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import { SignatureChecker } from "@openzeppelin/contracts/utils/cryptography/SignatureChecker.sol";

import { IProcurementEscrow } from "./interfaces/IProcurementEscrow.sol";

/// @title PoG Registry
/// @notice Authoritative project, procurement, evidence, and AI-assessment lifecycle.
/// @dev Custody, human approvals, reservations, and payments are intentionally out of M1.
contract PoGRegistry is Ownable2Step, Pausable, EIP712, ReentrancyGuard {
    enum ProcurementStatus {
        Draft,
        PreEvidenceRecorded,
        PreAssessed,
        ReserveApprovalPending,
        BudgetReserved,
        POIssued,
        Delivered,
        InvoiceSubmitted,
        FinalAssessed,
        PaymentApprovalPending,
        Paid,
        Cancelled
    }

    enum AssessmentStage {
        PreProcurement,
        FinalPayment
    }

    enum AssessmentOutcome {
        Pass,
        Review,
        Reject
    }

    struct RegistryProject {
        bytes32 projectId;
        address foundation;
        address recipient;
        address asset;
        uint8 assetDecimals;
        bytes32 allocationRoot;
        uint64 allocationRootVersion;
        uint32 approverEpoch;
        uint16 approvalThreshold;
        uint64 createdAt;
    }

    struct Procurement {
        bytes32 procurementId;
        bytes32 projectId;
        address vendor;
        uint256 budgetCap;
        bytes32 requestHash;
        bytes32 quoteBundleHash;
        bytes32 preEvidenceHash;
        bytes32 preAssessmentId;
        uint256 reservedAmount;
        bytes32 poHash;
        bytes32 grnHash;
        bytes32 invoiceHash;
        uint256 invoiceAmount;
        bytes32 finalEvidenceHash;
        bytes32 finalAssessmentId;
        uint256 paidAmount;
        ProcurementStatus status;
        uint64 createdAt;
    }

    struct AIAssessment {
        bytes32 assessmentId;
        bytes32 procurementId;
        AssessmentStage stage;
        bytes32 reportHash;
        bytes32 evidenceHash;
        bytes32 modelId;
        AssessmentOutcome outcome;
        uint16 riskScoreBps;
        uint64 issuedAt;
        uint64 expiresAt;
        uint256 nonce;
        address aiSigner;
    }

    error ZeroAddress();
    error ZeroId();
    error ZeroHash();
    error ZeroAmount();
    error EscrowNotBound();
    error EscrowAlreadyBound();
    error InvalidEscrow();
    error InvalidEscrowRegistry(address expected, address actual);
    error UnauthorizedCaller(address caller);
    error UnknownProject(bytes32 projectId);
    error DuplicateProject(bytes32 projectId);
    error UnknownProcurement(bytes32 procurementId);
    error DuplicateProcurement(bytes32 procurementId);
    error UnsupportedAsset(address asset);
    error UnsupportedAssetDecimals(uint8 actual);
    error InvalidApprovalPolicy();
    error DuplicateApprover(address approver);
    error PolicyChangeWhileApprovalPending(bytes32 procurementId);
    error WrongState(bytes32 procurementId, ProcurementStatus expected, ProcurementStatus actual);
    error TerminalState(bytes32 procurementId);
    error InvalidAssessmentStage();
    error InvalidEvidenceHash(bytes32 expected, bytes32 actual);
    error InvalidAssessmentId(bytes32 expected, bytes32 actual);
    error InvalidRiskScore(uint16 riskScoreBps);
    error InvalidAssessmentWindow(uint64 issuedAt, uint64 expiresAt);
    error AssessmentExpired(uint64 expiresAt);
    error AssessmentNotCurrent(bytes32 assessmentId);
    error UnauthorizedAISigner(address signer);
    error InvalidSignature();
    error NonceMismatch(address signer, uint256 expected, uint256 actual);
    error DuplicateAssessment(bytes32 assessmentId);
    error InvalidReserveAmount(uint256 amount, uint256 budgetCap);
    error InvalidInvoiceAmount(uint256 amount, uint256 reservedAmount);
    error InvalidPaidAmount(uint256 expected, uint256 actual);
    error CancellationReleaseMismatch(uint256 expected, uint256 actual);
    error NoPaidProcurement(bytes32 projectId);
    error InvalidAllocationRootVersion(uint64 expected, uint64 actual);

    event EscrowBound(address indexed escrow);
    event AISignerUpdated(address indexed signer, bool allowed);
    event ProjectCreated(
        bytes32 indexed projectId, address indexed foundation, address indexed asset
    );
    event ProcurementCreated(
        bytes32 indexed procurementId,
        bytes32 indexed projectId,
        address indexed vendor,
        uint256 budgetCap
    );
    event PrePurchaseEvidenceRecorded(
        bytes32 indexed procurementId,
        bytes32 requestHash,
        bytes32 quoteBundleHash,
        bytes32 preEvidenceHash
    );
    event AIAssessmentRecorded(
        bytes32 indexed assessmentId,
        bytes32 indexed procurementId,
        uint8 indexed stage,
        uint8 outcome,
        uint16 riskScoreBps,
        bytes32 reportHash,
        bytes32 evidenceHash
    );
    event PurchaseOrderRecorded(bytes32 indexed procurementId, bytes32 poHash);
    event DeliveryRecorded(bytes32 indexed procurementId, bytes32 grnHash);
    event InvoiceRecorded(
        bytes32 indexed procurementId,
        bytes32 invoiceHash,
        uint256 amount,
        bytes32 finalEvidenceHash
    );
    event ProcurementCancelled(bytes32 indexed procurementId, uint256 releasedAmount);
    event AllocationRootPublished(bytes32 indexed projectId, bytes32 indexed root, uint64 version);
    event ApprovalPolicyUpdated(
        bytes32 indexed projectId, uint32 indexed approverEpoch, uint16 threshold
    );

    uint8 public constant REQUIRED_ASSET_DECIMALS = 6;
    uint16 public constant MAX_RISK_SCORE_BPS = 10_000;
    uint32 public constant INITIAL_APPROVER_EPOCH = 1;
    string public constant PRE_EVIDENCE_DOMAIN = "POG_PRE_EVIDENCE_V1";
    string public constant FINAL_EVIDENCE_DOMAIN = "POG_FINAL_EVIDENCE_V1";

    bytes32 public constant AI_ASSESSMENT_TYPEHASH = keccak256(
        "AIAssessment(bytes32 assessmentId,bytes32 procurementId,uint8 stage,bytes32 reportHash,bytes32 evidenceHash,bytes32 modelId,uint8 outcome,uint16 riskScoreBps,uint64 issuedAt,uint64 expiresAt,uint256 nonce,address aiSigner)"
    );

    address public escrow;
    uint256 public projectCount;

    mapping(bytes32 projectId => RegistryProject) private _projects;
    mapping(bytes32 projectId => bool) private _projectExists;
    mapping(bytes32 procurementId => Procurement) private _procurements;
    mapping(bytes32 procurementId => bool) private _procurementExists;
    mapping(bytes32 assessmentId => AIAssessment) private _assessments;
    mapping(bytes32 assessmentId => bool) private _assessmentExists;
    mapping(address signer => bool) public aiSigners;
    mapping(address signer => uint256) public aiNonces;
    mapping(bytes32 projectId => bytes32[]) private _projectProcurements;
    mapping(bytes32 projectId => uint256) public paidProcurementCount;

    modifier onlyBound() {
        if (escrow == address(0)) revert EscrowNotBound();
        _;
    }

    modifier onlyEscrow() {
        if (msg.sender != escrow || escrow == address(0)) {
            revert UnauthorizedCaller(msg.sender);
        }
        _;
    }

    modifier onlyFoundation(bytes32 projectId) {
        RegistryProject storage project = _requireProject(projectId);
        if (msg.sender != project.foundation) revert UnauthorizedCaller(msg.sender);
        _;
    }

    constructor(address initialOwner) Ownable(initialOwner) EIP712("PoGRegistry", "1") {
        if (initialOwner == address(0)) revert ZeroAddress();
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function setAISigner(address signer, bool allowed) external onlyOwner {
        if (signer == address(0)) revert ZeroAddress();
        aiSigners[signer] = allowed;
        emit AISignerUpdated(signer, allowed);
    }

    function bindEscrow(address escrow_) external onlyOwner {
        if (escrow != address(0) || projectCount != 0) revert EscrowAlreadyBound();
        if (escrow_ == address(0) || escrow_.code.length == 0) revert InvalidEscrow();

        address reportedRegistry = IProcurementEscrow(escrow_).registry();
        if (reportedRegistry != address(this)) {
            revert InvalidEscrowRegistry(address(this), reportedRegistry);
        }

        escrow = escrow_;
        emit EscrowBound(escrow_);
    }

    function createProject(
        bytes32 projectId,
        address recipient,
        address asset,
        address[] calldata approvers,
        uint16 threshold
    ) external nonReentrant onlyBound whenNotPaused returns (bytes32) {
        if (projectId == bytes32(0)) revert ZeroId();
        if (_projectExists[projectId]) revert DuplicateProject(projectId);
        if (recipient == address(0) || asset == address(0)) revert ZeroAddress();
        _validateApprovalPolicy(approvers, threshold);

        uint8 assetDecimals = _readAssetDecimals(asset);
        _projects[projectId] = RegistryProject({
            projectId: projectId,
            foundation: msg.sender,
            recipient: recipient,
            asset: asset,
            assetDecimals: assetDecimals,
            allocationRoot: bytes32(0),
            allocationRootVersion: 0,
            approverEpoch: INITIAL_APPROVER_EPOCH,
            approvalThreshold: threshold,
            createdAt: _timestamp()
        });
        _projectExists[projectId] = true;
        projectCount += 1;

        IProcurementEscrow(escrow)
            .registerProjectFromRegistry(
                projectId, asset, assetDecimals, approvers, threshold, INITIAL_APPROVER_EPOCH
            );

        // Hook success is required before this event; nonReentrant prevents reordering.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ProjectCreated(projectId, msg.sender, asset);
        return projectId;
    }

    function createProcurement(
        bytes32 procurementId,
        bytes32 projectId,
        address vendor,
        uint256 budgetCap
    ) external onlyBound whenNotPaused onlyFoundation(projectId) returns (bytes32) {
        if (procurementId == bytes32(0)) revert ZeroId();
        if (_procurementExists[procurementId]) revert DuplicateProcurement(procurementId);
        if (vendor == address(0)) revert ZeroAddress();
        if (budgetCap == 0) revert ZeroAmount();

        _procurements[procurementId] = Procurement({
            procurementId: procurementId,
            projectId: projectId,
            vendor: vendor,
            budgetCap: budgetCap,
            requestHash: bytes32(0),
            quoteBundleHash: bytes32(0),
            preEvidenceHash: bytes32(0),
            preAssessmentId: bytes32(0),
            reservedAmount: 0,
            poHash: bytes32(0),
            grnHash: bytes32(0),
            invoiceHash: bytes32(0),
            invoiceAmount: 0,
            finalEvidenceHash: bytes32(0),
            finalAssessmentId: bytes32(0),
            paidAmount: 0,
            status: ProcurementStatus.Draft,
            createdAt: _timestamp()
        });
        _procurementExists[procurementId] = true;
        _projectProcurements[projectId].push(procurementId);

        emit ProcurementCreated(procurementId, projectId, vendor, budgetCap);
        return procurementId;
    }

    function recordPrePurchaseEvidence(
        bytes32 procurementId,
        bytes32 requestHash,
        bytes32 quoteBundleHash
    ) external whenNotPaused onlyFoundation(_requireProcurement(procurementId).projectId) {
        Procurement storage procurement = _procurements[procurementId];
        _requireState(procurement, ProcurementStatus.Draft);
        if (requestHash == bytes32(0) || quoteBundleHash == bytes32(0)) revert ZeroHash();

        bytes32 evidenceHash = _computePreEvidenceHash(procurement, requestHash, quoteBundleHash);
        procurement.requestHash = requestHash;
        procurement.quoteBundleHash = quoteBundleHash;
        procurement.preEvidenceHash = evidenceHash;
        procurement.status = ProcurementStatus.PreEvidenceRecorded;

        emit PrePurchaseEvidenceRecorded(procurementId, requestHash, quoteBundleHash, evidenceHash);
    }

    function submitAIAssessment(AIAssessment calldata assessment, bytes calldata signature)
        external
        nonReentrant
        onlyBound
        whenNotPaused
    {
        Procurement storage procurement = _requireProcurement(assessment.procurementId);
        _validateAssessmentFields(assessment);

        if (assessment.stage == AssessmentStage.PreProcurement) {
            _requireState(procurement, ProcurementStatus.PreEvidenceRecorded);
            if (assessment.evidenceHash != procurement.preEvidenceHash) {
                revert InvalidEvidenceHash(procurement.preEvidenceHash, assessment.evidenceHash);
            }
        } else if (assessment.stage == AssessmentStage.FinalPayment) {
            _requireState(procurement, ProcurementStatus.InvoiceSubmitted);
            if (assessment.evidenceHash != procurement.finalEvidenceHash) {
                revert InvalidEvidenceHash(procurement.finalEvidenceHash, assessment.evidenceHash);
            }
        } else {
            revert InvalidAssessmentStage();
        }

        bytes32 expectedId = computeAssessmentId(assessment);
        if (assessment.assessmentId != expectedId) {
            revert InvalidAssessmentId(expectedId, assessment.assessmentId);
        }
        if (_assessmentExists[assessment.assessmentId]) {
            revert DuplicateAssessment(assessment.assessmentId);
        }

        uint256 expectedNonce = aiNonces[assessment.aiSigner];
        if (assessment.nonce != expectedNonce) {
            revert NonceMismatch(assessment.aiSigner, expectedNonce, assessment.nonce);
        }
        if (!aiSigners[assessment.aiSigner]) {
            revert UnauthorizedAISigner(assessment.aiSigner);
        }
        if (!SignatureChecker.isValidSignatureNow(
                assessment.aiSigner, hashAIAssessment(assessment), signature
            )) {
            revert InvalidSignature();
        }

        aiNonces[assessment.aiSigner] = expectedNonce + 1;
        _assessments[assessment.assessmentId] = assessment;
        _assessmentExists[assessment.assessmentId] = true;

        if (assessment.stage == AssessmentStage.PreProcurement) {
            procurement.preAssessmentId = assessment.assessmentId;
            procurement.status = ProcurementStatus.PreAssessed;
        } else {
            procurement.finalAssessmentId = assessment.assessmentId;
            procurement.status = ProcurementStatus.FinalAssessed;
        }

        // SignatureChecker may call ERC-1271; nonReentrant protects event ordering.
        _emitAIAssessmentRecorded(assessment);
    }

    function recordPurchaseOrder(bytes32 procurementId, bytes32 poHash)
        external
        whenNotPaused
        onlyFoundation(_requireProcurement(procurementId).projectId)
    {
        Procurement storage procurement = _procurements[procurementId];
        _requireState(procurement, ProcurementStatus.BudgetReserved);
        if (poHash == bytes32(0)) revert ZeroHash();
        procurement.poHash = poHash;
        procurement.status = ProcurementStatus.POIssued;
        emit PurchaseOrderRecorded(procurementId, poHash);
    }

    function recordDelivery(bytes32 procurementId, bytes32 grnHash)
        external
        whenNotPaused
        onlyFoundation(_requireProcurement(procurementId).projectId)
    {
        Procurement storage procurement = _procurements[procurementId];
        _requireState(procurement, ProcurementStatus.POIssued);
        if (grnHash == bytes32(0)) revert ZeroHash();
        procurement.grnHash = grnHash;
        procurement.status = ProcurementStatus.Delivered;
        emit DeliveryRecorded(procurementId, grnHash);
    }

    function recordInvoice(bytes32 procurementId, bytes32 invoiceHash, uint256 amount)
        external
        whenNotPaused
        onlyFoundation(_requireProcurement(procurementId).projectId)
    {
        Procurement storage procurement = _procurements[procurementId];
        _requireState(procurement, ProcurementStatus.Delivered);
        if (invoiceHash == bytes32(0)) revert ZeroHash();
        if (amount == 0 || amount > procurement.reservedAmount) {
            revert InvalidInvoiceAmount(amount, procurement.reservedAmount);
        }

        procurement.invoiceHash = invoiceHash;
        procurement.invoiceAmount = amount;
        procurement.finalEvidenceHash = _computeFinalEvidenceHash(procurement);
        procurement.status = ProcurementStatus.InvoiceSubmitted;

        emit InvoiceRecorded(procurementId, invoiceHash, amount, procurement.finalEvidenceHash);
    }

    function setApprovalPolicy(bytes32 projectId, address[] calldata approvers, uint16 threshold)
        external
        nonReentrant
        whenNotPaused
        onlyFoundation(projectId)
    {
        _validateApprovalPolicy(approvers, threshold);
        bytes32[] storage ids = _projectProcurements[projectId];
        for (uint256 i; i < ids.length; ++i) {
            ProcurementStatus status = _procurements[ids[i]].status;
            if (
                status == ProcurementStatus.ReserveApprovalPending
                    || status == ProcurementStatus.PaymentApprovalPending
            ) {
                // The loop intentionally validates every project procurement.
                // forge-lint: disable-next-line(require-revert-in-loop)
                revert PolicyChangeWhileApprovalPending(ids[i]);
            }
        }

        RegistryProject storage project = _projects[projectId];
        uint32 newEpoch = project.approverEpoch + 1;
        IProcurementEscrow(escrow)
            .updateApprovalPolicyFromRegistry(projectId, approvers, threshold, newEpoch);
        project.approverEpoch = newEpoch;
        project.approvalThreshold = threshold;
        // Hook success is required before this event; nonReentrant prevents reordering.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ApprovalPolicyUpdated(projectId, newEpoch, threshold);
    }

    function cancelProcurement(bytes32 procurementId)
        external
        nonReentrant
        onlyFoundation(_requireProcurement(procurementId).projectId)
    {
        Procurement storage procurement = _procurements[procurementId];
        if (
            procurement.status == ProcurementStatus.Paid
                || procurement.status == ProcurementStatus.Cancelled
        ) {
            revert TerminalState(procurementId);
        }

        uint256 expectedRelease = procurement.reservedAmount;
        procurement.status = ProcurementStatus.Cancelled;
        uint256 released =
            IProcurementEscrow(escrow).releaseOnCancellationFromRegistry(procurementId);
        if (released != expectedRelease) {
            revert CancellationReleaseMismatch(expectedRelease, released);
        }
        // Hook success is required before this event; nonReentrant prevents reordering.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ProcurementCancelled(procurementId, released);
    }

    function publishAllocationRoot(bytes32 projectId, bytes32 root, uint64 version)
        external
        whenNotPaused
        onlyFoundation(projectId)
    {
        if (root == bytes32(0)) revert ZeroHash();
        if (paidProcurementCount[projectId] == 0) revert NoPaidProcurement(projectId);
        RegistryProject storage project = _projects[projectId];
        uint64 expectedVersion = project.allocationRootVersion + 1;
        if (version != expectedVersion) {
            revert InvalidAllocationRootVersion(expectedVersion, version);
        }
        project.allocationRoot = root;
        project.allocationRootVersion = version;
        emit AllocationRootPublished(projectId, root, version);
    }

    function markReserveApprovalPendingFromEscrow(bytes32 procurementId)
        external
        nonReentrant
        onlyEscrow
        whenNotPaused
    {
        Procurement storage procurement = _requireProcurement(procurementId);
        _requireState(procurement, ProcurementStatus.PreAssessed);
        _requireCurrentAssessment(procurement.preAssessmentId);
        procurement.status = ProcurementStatus.ReserveApprovalPending;
    }

    function markReservedFromEscrow(bytes32 procurementId, uint256 reserveAmount)
        external
        nonReentrant
        onlyEscrow
        whenNotPaused
    {
        Procurement storage procurement = _requireProcurement(procurementId);
        _requireState(procurement, ProcurementStatus.ReserveApprovalPending);
        _requireCurrentAssessment(procurement.preAssessmentId);
        if (reserveAmount == 0 || reserveAmount > procurement.budgetCap) {
            revert InvalidReserveAmount(reserveAmount, procurement.budgetCap);
        }
        procurement.reservedAmount = reserveAmount;
        procurement.status = ProcurementStatus.BudgetReserved;
    }

    function markPaymentApprovalPendingFromEscrow(bytes32 procurementId)
        external
        nonReentrant
        onlyEscrow
        whenNotPaused
    {
        Procurement storage procurement = _requireProcurement(procurementId);
        _requireState(procurement, ProcurementStatus.FinalAssessed);
        _requireCurrentAssessment(procurement.finalAssessmentId);
        procurement.status = ProcurementStatus.PaymentApprovalPending;
    }

    function markPaidFromEscrow(bytes32 procurementId, uint256 paidAmount)
        external
        nonReentrant
        onlyEscrow
        whenNotPaused
    {
        Procurement storage procurement = _requireProcurement(procurementId);
        _requireState(procurement, ProcurementStatus.PaymentApprovalPending);
        _requireCurrentAssessment(procurement.finalAssessmentId);
        if (paidAmount == 0 || paidAmount != procurement.invoiceAmount) {
            revert InvalidPaidAmount(procurement.invoiceAmount, paidAmount);
        }
        procurement.paidAmount = paidAmount;
        procurement.status = ProcurementStatus.Paid;
        paidProcurementCount[procurement.projectId] += 1;
    }

    function getProject(bytes32 projectId) external view returns (RegistryProject memory) {
        return _requireProject(projectId);
    }

    function getProcurement(bytes32 procurementId) external view returns (Procurement memory) {
        return _requireProcurement(procurementId);
    }

    function getAssessment(bytes32 assessmentId) external view returns (AIAssessment memory) {
        if (!_assessmentExists[assessmentId]) revert AssessmentNotCurrent(assessmentId);
        return _assessments[assessmentId];
    }

    function getProjectProcurementIds(bytes32 projectId) external view returns (bytes32[] memory) {
        _requireProject(projectId);
        return _projectProcurements[projectId];
    }

    function projectExists(bytes32 projectId) external view returns (bool) {
        return _projectExists[projectId];
    }

    function procurementExists(bytes32 procurementId) external view returns (bool) {
        return _procurementExists[procurementId];
    }

    function assessmentExists(bytes32 assessmentId) external view returns (bool) {
        return _assessmentExists[assessmentId];
    }

    function computePreEvidenceHash(
        bytes32 procurementId,
        bytes32 requestHash,
        bytes32 quoteBundleHash
    ) external view returns (bytes32) {
        return _computePreEvidenceHash(
            _requireProcurement(procurementId), requestHash, quoteBundleHash
        );
    }

    function computeFinalEvidenceHash(bytes32 procurementId) external view returns (bytes32) {
        return _computeFinalEvidenceHash(_requireProcurement(procurementId));
    }

    function computeAssessmentId(AIAssessment calldata assessment) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                assessment.procurementId,
                assessment.stage,
                assessment.reportHash,
                assessment.evidenceHash,
                assessment.modelId,
                assessment.outcome,
                assessment.riskScoreBps,
                assessment.issuedAt,
                assessment.expiresAt,
                assessment.nonce,
                assessment.aiSigner
            )
        );
    }

    function hashAIAssessment(AIAssessment calldata assessment) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                AI_ASSESSMENT_TYPEHASH,
                assessment.assessmentId,
                assessment.procurementId,
                uint8(assessment.stage),
                assessment.reportHash,
                assessment.evidenceHash,
                assessment.modelId,
                uint8(assessment.outcome),
                assessment.riskScoreBps,
                assessment.issuedAt,
                assessment.expiresAt,
                assessment.nonce,
                assessment.aiSigner
            )
        );
        return _hashTypedDataV4(structHash);
    }

    function domainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    function isAssessmentCurrent(bytes32 assessmentId) public view returns (bool) {
        if (!_assessmentExists[assessmentId]) return false;
        AIAssessment storage assessment = _assessments[assessmentId];
        // Timestamp comparison is the frozen EIP-712 expiry rule.
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp > assessment.expiresAt) return false;
        Procurement storage procurement = _procurements[assessment.procurementId];
        if (procurement.status == ProcurementStatus.Cancelled) return false;
        if (assessment.stage == AssessmentStage.PreProcurement) {
            return procurement.preAssessmentId == assessmentId
                && procurement.preEvidenceHash == assessment.evidenceHash;
        }
        return procurement.finalAssessmentId == assessmentId
            && procurement.finalEvidenceHash == assessment.evidenceHash;
    }

    function currentAssessmentForStage(bytes32 procurementId, AssessmentStage stage)
        external
        view
        returns (bytes32 assessmentId, bool current)
    {
        Procurement storage procurement = _requireProcurement(procurementId);
        assessmentId = stage == AssessmentStage.PreProcurement
            ? procurement.preAssessmentId
            : procurement.finalAssessmentId;
        current = isAssessmentCurrent(assessmentId);
    }

    function _readAssetDecimals(address asset) private view returns (uint8 decimals_) {
        if (asset.code.length == 0) revert UnsupportedAsset(asset);
        try IERC20Metadata(asset).decimals() returns (uint8 value) {
            decimals_ = value;
        } catch {
            revert UnsupportedAsset(asset);
        }
        if (decimals_ != REQUIRED_ASSET_DECIMALS) {
            revert UnsupportedAssetDecimals(decimals_);
        }
    }

    function _emitAIAssessmentRecorded(AIAssessment calldata assessment) private {
        // Caller is nonReentrant and intentionally emits only after ERC-1271 success.
        // forgefmt: disable-start
        // forge-lint: disable-next-line(reentrancy-events)
        emit AIAssessmentRecorded(assessment.assessmentId, assessment.procurementId, uint8(assessment.stage), uint8(assessment.outcome), assessment.riskScoreBps, assessment.reportHash, assessment.evidenceHash);
        // forgefmt: disable-end
    }

    function _timestamp() private view returns (uint64) {
        // Unix time cannot approach uint64 max within this protocol's lifetime.
        // forge-lint: disable-next-line(unsafe-typecast)
        return uint64(block.timestamp);
    }

    function _validateApprovalPolicy(address[] calldata approvers, uint16 threshold) private pure {
        if (threshold == 0 || threshold > approvers.length) {
            revert InvalidApprovalPolicy();
        }
        for (uint256 i; i < approvers.length; ++i) {
            // Complete membership validation intentionally reverts inside the loop.
            // forge-lint: disable-next-line(require-revert-in-loop)
            if (approvers[i] == address(0)) revert ZeroAddress();
            for (uint256 j; j < i; ++j) {
                if (approvers[i] == approvers[j]) {
                    // Duplicate detection intentionally reverts inside the loop.
                    // forge-lint: disable-next-line(require-revert-in-loop)
                    revert DuplicateApprover(approvers[i]);
                }
            }
        }
    }

    function _validateAssessmentFields(AIAssessment calldata assessment) private view {
        if (assessment.aiSigner == address(0)) revert ZeroAddress();
        if (
            assessment.reportHash == bytes32(0) || assessment.evidenceHash == bytes32(0)
                || assessment.modelId == bytes32(0)
        ) revert ZeroHash();
        if (assessment.riskScoreBps > MAX_RISK_SCORE_BPS) {
            revert InvalidRiskScore(assessment.riskScoreBps);
        }
        // Timestamp comparison is the frozen issued-at validity rule.
        // forge-lint: disable-next-line(block-timestamp)
        if (assessment.expiresAt <= assessment.issuedAt || assessment.issuedAt > block.timestamp) {
            revert InvalidAssessmentWindow(assessment.issuedAt, assessment.expiresAt);
        }
        // Timestamp comparison is the frozen expiry rule.
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp > assessment.expiresAt) {
            revert AssessmentExpired(assessment.expiresAt);
        }
    }

    function _computePreEvidenceHash(
        Procurement storage procurement,
        bytes32 requestHash,
        bytes32 quoteBundleHash
    ) private view returns (bytes32) {
        RegistryProject storage project = _projects[procurement.projectId];
        return keccak256(
            abi.encode(
                PRE_EVIDENCE_DOMAIN,
                procurement.projectId,
                procurement.procurementId,
                procurement.vendor,
                project.asset,
                procurement.budgetCap,
                requestHash,
                quoteBundleHash
            )
        );
    }

    function _computeFinalEvidenceHash(Procurement storage procurement)
        private
        view
        returns (bytes32)
    {
        RegistryProject storage project = _projects[procurement.projectId];
        return keccak256(
            abi.encode(
                FINAL_EVIDENCE_DOMAIN,
                procurement.projectId,
                procurement.procurementId,
                procurement.vendor,
                project.asset,
                procurement.reservedAmount,
                procurement.poHash,
                procurement.grnHash,
                procurement.invoiceHash,
                procurement.invoiceAmount
            )
        );
    }

    function _requireCurrentAssessment(bytes32 assessmentId) private view {
        if (!isAssessmentCurrent(assessmentId)) {
            revert AssessmentNotCurrent(assessmentId);
        }
    }

    function _requireState(Procurement storage procurement, ProcurementStatus expected)
        private
        view
    {
        if (procurement.status != expected) {
            revert WrongState(procurement.procurementId, expected, procurement.status);
        }
    }

    function _requireProject(bytes32 projectId)
        private
        view
        returns (RegistryProject storage project)
    {
        if (!_projectExists[projectId]) revert UnknownProject(projectId);
        return _projects[projectId];
    }

    function _requireProcurement(bytes32 procurementId)
        private
        view
        returns (Procurement storage procurement)
    {
        if (!_procurementExists[procurementId]) {
            revert UnknownProcurement(procurementId);
        }
        return _procurements[procurementId];
    }
}
