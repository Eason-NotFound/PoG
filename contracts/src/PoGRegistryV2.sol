// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { Ownable2Step } from "@openzeppelin/contracts/access/Ownable2Step.sol";
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import { Pausable } from "@openzeppelin/contracts/utils/Pausable.sol";
import { ReentrancyGuard } from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import { EIP712 } from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import { SignatureChecker } from "@openzeppelin/contracts/utils/cryptography/SignatureChecker.sol";

import { IPoGRegistryV2 } from "./interfaces/IPoGRegistryV2.sol";
import { IProcurementEscrowV2 } from "./interfaces/IProcurementEscrowV2.sol";

/// @title PoG Registry V2
/// @notice V2 facts and lifecycle registry for Foundation release and donor refunds.
contract PoGRegistryV2 is IPoGRegistryV2, Ownable2Step, Pausable, EIP712, ReentrancyGuard {
    struct AssessmentInput {
        AssessmentStage stage;
        bytes32 procurementId;
        bytes32 assessmentId;
        AssessmentOutcome outcome;
        uint16 riskScoreBps;
        bytes32 evidenceHash;
        bytes32 reportHash;
        address signer;
        uint256 nonce;
        uint64 deadline;
    }

    struct RecipientReceiptInput {
        bytes32 projectId;
        bytes32 procurementId;
        address expectedRecipient;
        address vendor;
        bytes32 poHash;
        bytes32 invoiceHash;
        uint256 invoiceAmount;
        bytes32 goodsHash;
        bytes32 receiptEvidenceHash;
        uint256 nonce;
        uint64 deadline;
    }

    error ZeroAddress();
    error ZeroId();
    error ZeroHash();
    error ZeroAmount();
    error EscrowNotBound();
    error EscrowAlreadyBound();
    error InvalidEscrow();
    error InvalidEscrowRegistry(address expected, address actual);
    error Unauthorized(address caller);
    error UnknownProject(bytes32 projectId);
    error DuplicateProject(bytes32 projectId);
    error UnknownProcurement(bytes32 procurementId);
    error DuplicateProcurement(bytes32 procurementId);
    error DuplicateAssessment(bytes32 assessmentId);
    error InvalidAssetDecimals(uint8 actual);
    error InvalidPolicy();
    error WrongState(uint8 expected, uint8 actual);
    error InvalidAmount(uint256 amount);
    error InvalidEvidence(bytes32 expected, bytes32 actual);
    error InvalidAssessmentId(bytes32 expected, bytes32 actual);
    error InvalidAssessmentStage();
    error InvalidRiskScore(uint16 riskScoreBps);
    error UnauthorizedAISigner(address signer);
    error InvalidSignature();
    error NonceMismatch(address signer, uint256 expected, uint256 actual);
    error SignatureExpired(uint64 deadline);
    error ReturnedFundsBlockSettlement(uint256 returnedAmount);
    error EscrowInteractionInProgress();

    event EscrowBound(address indexed escrow);
    event AISignerUpdated(address indexed signer, bool allowed);
    event ProjectCreated(
        bytes32 indexed projectId,
        address indexed foundation,
        address indexed recipient,
        address asset,
        uint16 threshold
    );
    event ProjectClosingRequested(bytes32 indexed projectId);
    event ProjectRefundable(bytes32 indexed projectId);
    event ProjectClosed(bytes32 indexed projectId);
    event ApprovalPolicyUpdated(
        bytes32 indexed projectId, uint32 indexed policyEpoch, uint16 threshold
    );
    event ProcurementCreated(
        bytes32 indexed procurementId,
        bytes32 indexed projectId,
        address indexed vendor,
        uint256 budgetCap
    );
    event PurchaseOrderRecorded(
        bytes32 indexed procurementId,
        bytes32 poHash,
        bytes32 requestHash,
        bytes32 goodsRequestHash,
        bytes32 preEvidenceHash
    );
    event AIAssessmentRecorded(
        bytes32 indexed assessmentId,
        bytes32 indexed procurementId,
        AssessmentStage indexed stage,
        AssessmentOutcome outcome,
        uint16 riskScoreBps,
        bytes32 evidenceHash,
        bytes32 reportHash,
        address signer,
        uint64 deadline
    );
    event InvoiceAndGoodsRecorded(
        bytes32 indexed procurementId, bytes32 invoiceHash, uint256 invoiceAmount, bytes32 goodsHash
    );
    event RecipientReceiptAccepted(
        bytes32 indexed procurementId,
        address indexed recipient,
        bytes32 receiptDigest,
        bytes32 receiptEvidenceHash
    );
    event SettlementEvidenceRecorded(
        bytes32 indexed procurementId,
        bytes32 conversionEvidenceHash,
        bytes32 paymentEvidenceHash,
        bytes32 settlementHash
    );
    event ProcurementStateChanged(
        bytes32 indexed procurementId, ProcurementState previousState, ProcurementState newState
    );
    event ReturnedFundsNoted(bytes32 indexed procurementId, uint256 cumulativeReturned);

    uint8 public constant REQUIRED_ASSET_DECIMALS = 6;
    uint16 public constant MAX_APPROVERS = 16;
    uint16 public constant MAX_RISK_SCORE_BPS = 10_000;
    uint32 public constant INITIAL_POLICY_EPOCH = 1;

    bytes32 public constant AI_ASSESSMENT_TYPEHASH = keccak256(
        "AIAssessment(uint8 stage,bytes32 procurementId,bytes32 assessmentId,uint8 outcome,uint16 riskScoreBps,bytes32 evidenceHash,bytes32 reportHash,address signer,uint256 nonce,uint64 deadline)"
    );
    bytes32 public constant RECIPIENT_RECEIPT_TYPEHASH = keccak256(
        "RecipientReceipt(bytes32 projectId,bytes32 procurementId,address expectedRecipient,address vendor,bytes32 poHash,bytes32 invoiceHash,uint256 invoiceAmount,bytes32 goodsHash,bytes32 receiptEvidenceHash,uint256 nonce,uint64 deadline)"
    );
    bytes32 public constant PRE_EVIDENCE_DOMAIN = keccak256("POG_V2_PRE_EVIDENCE");
    bytes32 public constant FINAL_EVIDENCE_DOMAIN = keccak256("POG_V2_FINAL_EVIDENCE");
    bytes32 public constant SETTLEMENT_DOMAIN = keccak256("POG_V2_SETTLEMENT_EVIDENCE");

    address public override escrow;
    uint256 public projectCount;

    mapping(bytes32 => ProjectView) private _projects;
    mapping(bytes32 => bool) private _projectExists;
    mapping(bytes32 => ProcurementView) private _procurements;
    mapping(bytes32 => bool) private _procurementExists;
    mapping(bytes32 => AssessmentView) private _assessments;
    mapping(bytes32 => bool) private _assessmentExists;
    mapping(address => bool) public override aiSigners;
    mapping(address => uint256) public aiNonces;
    mapping(address => uint256) public recipientNonces;

    modifier onlyBound() {
        if (escrow == address(0)) revert EscrowNotBound();
        _;
    }

    modifier onlyEscrow() {
        if (msg.sender != escrow || escrow == address(0)) revert Unauthorized(msg.sender);
        _;
    }

    modifier onlyFoundation(bytes32 projectId) {
        if (!_projectExists[projectId]) revert UnknownProject(projectId);
        if (msg.sender != _projects[projectId].foundation) revert Unauthorized(msg.sender);
        _;
    }

    modifier escrowIdle() {
        if (escrow != address(0) && IProcurementEscrowV2(escrow).busy()) {
            revert EscrowInteractionInProgress();
        }
        _;
    }

    constructor(address initialOwner) Ownable(initialOwner) EIP712("PoGRegistryV2", "2") {
        if (initialOwner == address(0)) revert ZeroAddress();
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function paused() public view override(IPoGRegistryV2, Pausable) returns (bool) {
        return super.paused();
    }

    function setAISigner(address signer, bool allowed) external onlyOwner escrowIdle {
        if (signer == address(0)) revert ZeroAddress();
        aiSigners[signer] = allowed;
        emit AISignerUpdated(signer, allowed);
    }

    function bindEscrow(address escrow_) external onlyOwner {
        if (escrow != address(0) || projectCount != 0) revert EscrowAlreadyBound();
        if (escrow_ == address(0) || escrow_.code.length == 0) revert InvalidEscrow();
        address reported = IProcurementEscrowV2(escrow_).registry();
        if (reported != address(this)) revert InvalidEscrowRegistry(address(this), reported);
        escrow = escrow_;
        emit EscrowBound(escrow_);
    }

    function createProject(
        bytes32 projectId,
        address recipient,
        address asset,
        address[] calldata approvers,
        uint16 threshold
    ) external nonReentrant onlyBound whenNotPaused escrowIdle returns (bytes32) {
        if (projectId == bytes32(0)) revert ZeroId();
        if (_projectExists[projectId]) revert DuplicateProject(projectId);
        if (recipient == address(0) || asset == address(0)) revert ZeroAddress();
        _validatePolicy(approvers, threshold);
        uint8 decimals = IERC20Metadata(asset).decimals();
        if (decimals != REQUIRED_ASSET_DECIMALS) revert InvalidAssetDecimals(decimals);

        _projects[projectId] = ProjectView({
            projectId: projectId,
            foundation: msg.sender,
            recipient: recipient,
            asset: asset,
            assetDecimals: decimals,
            policyEpoch: INITIAL_POLICY_EPOCH,
            threshold: threshold,
            state: ProjectState.Active,
            unresolvedProcurements: 0,
            // Timestamps cannot approach uint64 capacity in the lifetime of this contract.
            // forge-lint: disable-next-line(unsafe-typecast)
            createdAt: uint64(block.timestamp)
        });
        _projectExists[projectId] = true;
        projectCount++;
        IProcurementEscrowV2(escrow)
            .registerProjectFromRegistry(
                projectId, asset, approvers, threshold, INITIAL_POLICY_EPOCH
            );
        // Registry is guarded; the exact-counterpart registration hook calls no untrusted code.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ProjectCreated(projectId, msg.sender, recipient, asset, threshold);
        return projectId;
    }

    function updateApprovalPolicy(bytes32 projectId, address[] calldata approvers, uint16 threshold)
        external
        nonReentrant
        onlyBound
        onlyFoundation(projectId)
        escrowIdle
    {
        _validatePolicy(approvers, threshold);
        ProjectView storage project = _projects[projectId];
        if (project.state == ProjectState.Refundable || project.state == ProjectState.Closed) {
            revert WrongState(uint8(ProjectState.Active), uint8(project.state));
        }
        uint32 nextEpoch = project.policyEpoch + 1;
        project.policyEpoch = nextEpoch;
        project.threshold = threshold;
        IProcurementEscrowV2(escrow)
            .updatePolicyFromRegistry(projectId, approvers, threshold, nextEpoch);
        // The atomic counterpart hook completed before the canonical synchronized event.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ApprovalPolicyUpdated(projectId, nextEpoch, threshold);
    }

    function createProcurement(
        bytes32 procurementId,
        bytes32 projectId,
        address vendor,
        uint256 budgetCap
    ) external onlyBound whenNotPaused escrowIdle onlyFoundation(projectId) returns (bytes32) {
        if (procurementId == bytes32(0)) revert ZeroId();
        if (_procurementExists[procurementId]) revert DuplicateProcurement(procurementId);
        if (_projects[projectId].state != ProjectState.Active) {
            revert WrongState(uint8(ProjectState.Active), uint8(_projects[projectId].state));
        }
        if (vendor == address(0)) revert ZeroAddress();
        if (budgetCap == 0) revert ZeroAmount();
        ProcurementView storage procurement = _procurements[procurementId];
        procurement.procurementId = procurementId;
        procurement.projectId = projectId;
        procurement.vendor = vendor;
        procurement.budgetCap = budgetCap;
        procurement.state = ProcurementState.Created;
        // ProcurementCreated below is the corresponding access-control membership event.
        // forge-lint: disable-next-line(missing-events-access-control)
        _procurementExists[procurementId] = true;
        _projects[projectId].unresolvedProcurements++;
        emit ProcurementCreated(procurementId, projectId, vendor, budgetCap);
        return procurementId;
    }

    function recordPurchaseOrder(
        bytes32 procurementId,
        bytes32 poHash,
        bytes32 requestHash,
        bytes32 goodsRequestHash
    ) external whenNotPaused escrowIdle {
        ProcurementView storage procurement = _foundationProcurement(procurementId);
        _expect(procurement, ProcurementState.Created);
        if (poHash == bytes32(0) || requestHash == bytes32(0) || goodsRequestHash == bytes32(0)) {
            revert ZeroHash();
        }
        procurement.poHash = poHash;
        procurement.requestHash = requestHash;
        procurement.goodsRequestHash = goodsRequestHash;
        procurement.preEvidenceHash = computePreEvidenceHash(procurementId);
        _transition(procurement, ProcurementState.PORecorded);
        emit PurchaseOrderRecorded(
            procurementId, poHash, requestHash, goodsRequestHash, procurement.preEvidenceHash
        );
    }

    function submitAIAssessment(AssessmentInput calldata input, bytes calldata signature)
        external
        whenNotPaused
        escrowIdle
        returns (bytes32)
    {
        if (!_procurementExists[input.procurementId]) {
            revert UnknownProcurement(input.procurementId);
        }
        ProcurementView storage procurement = _procurements[input.procurementId];
        bytes32 expectedEvidence;
        ProcurementState nextState;
        if (input.stage == AssessmentStage.PrePurchase) {
            if (
                procurement.state != ProcurementState.PORecorded
                    && procurement.state != ProcurementState.PreAssessed
                    && procurement.state != ProcurementState.ReserveApprovalPending
            ) revert InvalidAssessmentStage();
            expectedEvidence = procurement.preEvidenceHash;
            nextState = ProcurementState.PreAssessed;
        } else {
            if (
                procurement.state != ProcurementState.ReceiptConfirmed
                    && procurement.state != ProcurementState.FinalAssessed
                    && procurement.state != ProcurementState.ReleaseApprovalPending
            ) revert InvalidAssessmentStage();
            expectedEvidence = procurement.finalEvidenceHash;
            nextState = ProcurementState.FinalAssessed;
        }
        if (input.riskScoreBps > MAX_RISK_SCORE_BPS) {
            revert InvalidRiskScore(input.riskScoreBps);
        }
        if (input.evidenceHash != expectedEvidence) {
            revert InvalidEvidence(expectedEvidence, input.evidenceHash);
        }
        if (input.assessmentId == bytes32(0) || input.reportHash == bytes32(0)) revert ZeroHash();
        if (_assessmentExists[input.assessmentId]) revert DuplicateAssessment(input.assessmentId);
        if (!aiSigners[input.signer]) revert UnauthorizedAISigner(input.signer);
        // Deadline checks are the intended use of timestamp; small validator drift is harmless.
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp > input.deadline) revert SignatureExpired(input.deadline);
        uint256 expectedNonce = aiNonces[input.signer];
        if (input.nonce != expectedNonce) {
            revert NonceMismatch(input.signer, expectedNonce, input.nonce);
        }
        bytes32 expectedId = computeAssessmentId(input);
        if (input.assessmentId != expectedId) {
            revert InvalidAssessmentId(expectedId, input.assessmentId);
        }
        bytes32 digest = _hashTypedDataV4(_assessmentStructHash(input));
        if (!SignatureChecker.isValidSignatureNow(input.signer, digest, signature)) {
            revert InvalidSignature();
        }
        aiNonces[input.signer] = expectedNonce + 1;
        _assessmentExists[input.assessmentId] = true;
        _assessments[input.assessmentId] = AssessmentView({
            assessmentId: input.assessmentId,
            procurementId: input.procurementId,
            stage: input.stage,
            outcome: input.outcome,
            riskScoreBps: input.riskScoreBps,
            evidenceHash: input.evidenceHash,
            reportHash: input.reportHash,
            signer: input.signer,
            nonce: input.nonce,
            deadline: input.deadline
        });
        if (input.stage == AssessmentStage.PrePurchase) {
            procurement.preAssessmentId = input.assessmentId;
        } else {
            procurement.finalAssessmentId = input.assessmentId;
        }
        _transition(procurement, nextState);
        _emitAssessment(input);
        return input.assessmentId;
    }

    function recordInvoiceAndGoods(
        bytes32 procurementId,
        bytes32 invoiceHash,
        uint256 invoiceAmount,
        bytes32 goodsHash
    ) external whenNotPaused escrowIdle {
        ProcurementView storage procurement = _foundationProcurement(procurementId);
        _expect(procurement, ProcurementState.Reserved);
        if (invoiceHash == bytes32(0) || goodsHash == bytes32(0)) revert ZeroHash();
        if (invoiceAmount == 0 || invoiceAmount > procurement.reservedAmount) {
            revert InvalidAmount(invoiceAmount);
        }
        procurement.invoiceHash = invoiceHash;
        procurement.invoiceAmount = invoiceAmount;
        procurement.goodsHash = goodsHash;
        _transition(procurement, ProcurementState.InvoiceRecorded);
        emit InvoiceAndGoodsRecorded(procurementId, invoiceHash, invoiceAmount, goodsHash);
    }

    function submitRecipientReceipt(RecipientReceiptInput calldata input, bytes calldata signature)
        external
        whenNotPaused
        escrowIdle
        returns (bytes32 receiptDigest)
    {
        if (!_procurementExists[input.procurementId]) {
            revert UnknownProcurement(input.procurementId);
        }
        ProcurementView storage procurement = _procurements[input.procurementId];
        _expect(procurement, ProcurementState.InvoiceRecorded);
        ProjectView storage project = _projects[procurement.projectId];
        if (
            input.projectId != procurement.projectId || input.expectedRecipient != project.recipient
                || input.vendor != procurement.vendor || input.poHash != procurement.poHash
                || input.invoiceHash != procurement.invoiceHash
                || input.invoiceAmount != procurement.invoiceAmount
                || input.goodsHash != procurement.goodsHash
        ) revert InvalidEvidence(bytes32(0), bytes32(0));
        if (input.receiptEvidenceHash == bytes32(0)) revert ZeroHash();
        // Deadline checks are the intended use of timestamp; small validator drift is harmless.
        // forge-lint: disable-next-line(block-timestamp)
        if (block.timestamp > input.deadline) revert SignatureExpired(input.deadline);
        uint256 expectedNonce = recipientNonces[project.recipient];
        if (input.nonce != expectedNonce) {
            revert NonceMismatch(project.recipient, expectedNonce, input.nonce);
        }
        receiptDigest = _hashTypedDataV4(_receiptStructHash(input));
        if (!SignatureChecker.isValidSignatureNow(project.recipient, receiptDigest, signature)) {
            revert InvalidSignature();
        }
        recipientNonces[project.recipient] = expectedNonce + 1;
        procurement.receiptDigest = receiptDigest;
        procurement.finalEvidenceHash = computeFinalEvidenceHash(input.procurementId);
        _transition(procurement, ProcurementState.ReceiptConfirmed);
        // SignatureChecker is read-only for EOAs/ERC1271 and this function is state-isolated.
        // forge-lint: disable-start(reentrancy-events)
        emit RecipientReceiptAccepted(
            input.procurementId, project.recipient, receiptDigest, input.receiptEvidenceHash
        );
        // forge-lint: disable-end(reentrancy-events)
    }

    function recordSettlement(
        bytes32 procurementId,
        bytes32 conversionEvidenceHash,
        bytes32 paymentEvidenceHash
    ) external whenNotPaused escrowIdle {
        ProcurementView storage procurement = _foundationProcurement(procurementId);
        _expect(procurement, ProcurementState.FundsReleased);
        if (procurement.returnedAmount != 0) {
            revert ReturnedFundsBlockSettlement(procurement.returnedAmount);
        }
        if (conversionEvidenceHash == bytes32(0) || paymentEvidenceHash == bytes32(0)) {
            revert ZeroHash();
        }
        procurement.conversionEvidenceHash = conversionEvidenceHash;
        procurement.paymentEvidenceHash = paymentEvidenceHash;
        procurement.settlementHash = keccak256(
            abi.encode(
                SETTLEMENT_DOMAIN,
                procurement.projectId,
                procurement.procurementId,
                _projects[procurement.projectId].foundation,
                procurement.vendor,
                _projects[procurement.projectId].asset,
                procurement.invoiceAmount,
                conversionEvidenceHash,
                paymentEvidenceHash
            )
        );
        _transition(procurement, ProcurementState.SettlementRecorded);
        emit SettlementEvidenceRecorded(
            procurementId, conversionEvidenceHash, paymentEvidenceHash, procurement.settlementHash
        );
    }

    function cancelUnreserved(bytes32 procurementId) external nonReentrant escrowIdle {
        ProcurementView storage procurement = _foundationProcurement(procurementId);
        if (
            procurement.state != ProcurementState.Created
                && procurement.state != ProcurementState.PORecorded
                && procurement.state != ProcurementState.PreAssessed
                && procurement.state != ProcurementState.ReserveApprovalPending
        ) revert WrongState(uint8(ProcurementState.Created), uint8(procurement.state));
        IProcurementEscrowV2(escrow).cancelUnreservedFromRegistry(procurementId);
        _resolve(procurement, ProcurementState.Cancelled);
    }

    function requestClosing(bytes32 projectId)
        external
        whenNotPaused
        escrowIdle
        onlyFoundation(projectId)
    {
        ProjectView storage project = _projects[projectId];
        if (project.state != ProjectState.Active) {
            revert WrongState(uint8(ProjectState.Active), uint8(project.state));
        }
        project.state = ProjectState.Closing;
        emit ProjectClosingRequested(projectId);
    }

    function markReservePendingFromEscrow(bytes32 procurementId) external override onlyEscrow {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.PreAssessed);
        _transition(procurement, ProcurementState.ReserveApprovalPending);
    }

    function markReservedFromEscrow(bytes32 procurementId, uint256 amount)
        external
        override
        onlyEscrow
    {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.ReserveApprovalPending);
        if (amount == 0 || amount > procurement.budgetCap) revert InvalidAmount(amount);
        procurement.reservedAmount = amount;
        _transition(procurement, ProcurementState.Reserved);
    }

    function markReleasePendingFromEscrow(bytes32 procurementId) external override onlyEscrow {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.FinalAssessed);
        _transition(procurement, ProcurementState.ReleaseApprovalPending);
    }

    function markFundsReleasedFromEscrow(bytes32 procurementId) external override onlyEscrow {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.ReleaseApprovalPending);
        _transition(procurement, ProcurementState.FundsReleased);
    }

    function markSettlementPendingFromEscrow(bytes32 procurementId) external override onlyEscrow {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.SettlementRecorded);
        if (procurement.returnedAmount != 0) {
            revert ReturnedFundsBlockSettlement(procurement.returnedAmount);
        }
        _transition(procurement, ProcurementState.SettlementApprovalPending);
    }

    function markPaymentConfirmedFromEscrow(bytes32 procurementId) external override onlyEscrow {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.SettlementApprovalPending);
        if (procurement.returnedAmount != 0) {
            revert ReturnedFundsBlockSettlement(procurement.returnedAmount);
        }
        _resolve(procurement, ProcurementState.PaymentConfirmed);
    }

    function markCancellationPendingFromEscrow(bytes32 procurementId, bytes32 reasonHash)
        external
        override
        onlyEscrow
    {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        if (
            procurement.state != ProcurementState.Reserved
                && procurement.state != ProcurementState.InvoiceRecorded
        ) revert WrongState(uint8(ProcurementState.Reserved), uint8(procurement.state));
        if (reasonHash == bytes32(0)) revert ZeroHash();
        procurement.cancellationReasonHash = reasonHash;
        _transition(procurement, ProcurementState.CancellationApprovalPending);
    }

    function markCancelledFromEscrow(bytes32 procurementId) external override onlyEscrow {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        _expect(procurement, ProcurementState.CancellationApprovalPending);
        _resolve(procurement, ProcurementState.Cancelled);
    }

    function noteReturnedFundsFromEscrow(bytes32 procurementId, uint256 cumulativeReturned)
        external
        override
        onlyEscrow
    {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        if (
            procurement.state != ProcurementState.FundsReleased
                && procurement.state != ProcurementState.SettlementRecorded
                && procurement.state != ProcurementState.SettlementApprovalPending
        ) revert WrongState(uint8(ProcurementState.FundsReleased), uint8(procurement.state));
        if (
            cumulativeReturned <= procurement.returnedAmount
                || cumulativeReturned > procurement.invoiceAmount
        ) revert InvalidAmount(cumulativeReturned);
        procurement.returnedAmount = cumulativeReturned;
        emit ReturnedFundsNoted(procurementId, cumulativeReturned);
    }

    function markRefundableFromEscrow(bytes32 projectId) external override onlyEscrow {
        ProjectView storage project = _requireProject(projectId);
        if (project.state != ProjectState.Closing || project.unresolvedProcurements != 0) {
            revert WrongState(uint8(ProjectState.Closing), uint8(project.state));
        }
        project.state = ProjectState.Refundable;
        emit ProjectRefundable(projectId);
    }

    function markClosedFromEscrow(bytes32 projectId) external override onlyEscrow {
        ProjectView storage project = _requireProject(projectId);
        if (project.state != ProjectState.Closing && project.state != ProjectState.Refundable) {
            revert WrongState(uint8(ProjectState.Refundable), uint8(project.state));
        }
        if (project.unresolvedProcurements != 0) {
            revert InvalidAmount(project.unresolvedProcurements);
        }
        project.state = ProjectState.Closed;
        emit ProjectClosed(projectId);
    }

    function getProject(bytes32 projectId) external view override returns (ProjectView memory) {
        return _requireProject(projectId);
    }

    function getProcurement(bytes32 procurementId)
        external
        view
        override
        returns (ProcurementView memory)
    {
        return _requireProcurement(procurementId);
    }

    function getEscrowProcurement(bytes32 procurementId)
        external
        view
        override
        returns (EscrowProcurementView memory result)
    {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        result.procurementId = procurement.procurementId;
        result.projectId = procurement.projectId;
        result.vendor = procurement.vendor;
        result.budgetCap = procurement.budgetCap;
        result.preEvidenceHash = procurement.preEvidenceHash;
        result.preAssessmentId = procurement.preAssessmentId;
        result.reservedAmount = procurement.reservedAmount;
        result.invoiceAmount = procurement.invoiceAmount;
        result.finalEvidenceHash = procurement.finalEvidenceHash;
        result.finalAssessmentId = procurement.finalAssessmentId;
        result.settlementHash = procurement.settlementHash;
        result.cancellationEvidenceHash = keccak256(
            abi.encode(
                procurement.poHash,
                procurement.invoiceHash,
                procurement.invoiceAmount,
                procurement.goodsHash
            )
        );
        result.cancellationReasonHash = procurement.cancellationReasonHash;
        result.returnedAmount = procurement.returnedAmount;
        result.state = procurement.state;
    }

    function getAssessment(bytes32 assessmentId)
        external
        view
        override
        returns (AssessmentView memory)
    {
        if (!_assessmentExists[assessmentId]) {
            revert InvalidAssessmentId(bytes32(0), assessmentId);
        }
        return _assessments[assessmentId];
    }

    function escrowBusy() external view override returns (bool) {
        return escrow != address(0) && IProcurementEscrowV2(escrow).busy();
    }

    function computePreEvidenceHash(bytes32 procurementId) public view returns (bytes32) {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        ProjectView storage project = _projects[procurement.projectId];
        return keccak256(
            abi.encode(
                PRE_EVIDENCE_DOMAIN,
                procurement.projectId,
                procurementId,
                project.foundation,
                project.recipient,
                procurement.vendor,
                project.asset,
                procurement.budgetCap,
                procurement.poHash,
                procurement.requestHash,
                procurement.goodsRequestHash
            )
        );
    }

    function computeFinalEvidenceHash(bytes32 procurementId) public view returns (bytes32) {
        ProcurementView storage procurement = _requireProcurement(procurementId);
        ProjectView storage project = _projects[procurement.projectId];
        return keccak256(
            abi.encode(
                FINAL_EVIDENCE_DOMAIN,
                procurement.projectId,
                procurementId,
                project.foundation,
                project.recipient,
                procurement.vendor,
                project.asset,
                procurement.reservedAmount,
                procurement.poHash,
                procurement.invoiceHash,
                procurement.invoiceAmount,
                procurement.goodsHash,
                procurement.receiptDigest
            )
        );
    }

    function computeAssessmentId(AssessmentInput calldata input) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                keccak256("POG_V2_AI_ASSESSMENT_ID"),
                input.stage,
                input.procurementId,
                input.outcome,
                input.riskScoreBps,
                input.evidenceHash,
                input.reportHash,
                input.signer,
                input.nonce,
                input.deadline
            )
        );
    }

    function assessmentDigest(AssessmentInput calldata input) external view returns (bytes32) {
        return _hashTypedDataV4(_assessmentStructHash(input));
    }

    function _assessmentStructHash(AssessmentInput calldata input) private pure returns (bytes32) {
        return keccak256(
            abi.encode(
                AI_ASSESSMENT_TYPEHASH,
                input.stage,
                input.procurementId,
                input.assessmentId,
                input.outcome,
                input.riskScoreBps,
                input.evidenceHash,
                input.reportHash,
                input.signer,
                input.nonce,
                input.deadline
            )
        );
    }

    function _receiptStructHash(RecipientReceiptInput calldata input)
        private
        pure
        returns (bytes32)
    {
        return keccak256(
            abi.encode(
                RECIPIENT_RECEIPT_TYPEHASH,
                input.projectId,
                input.procurementId,
                input.expectedRecipient,
                input.vendor,
                input.poHash,
                input.invoiceHash,
                input.invoiceAmount,
                input.goodsHash,
                input.receiptEvidenceHash,
                input.nonce,
                input.deadline
            )
        );
    }

    function _emitAssessment(AssessmentInput calldata input) private {
        // SignatureChecker is read-only for EOAs/ERC1271 and assessment state is already committed.
        // forge-lint: disable-start(reentrancy-events)
        emit AIAssessmentRecorded(
            input.assessmentId,
            input.procurementId,
            input.stage,
            input.outcome,
            input.riskScoreBps,
            input.evidenceHash,
            input.reportHash,
            input.signer,
            input.deadline
        );
        // forge-lint: disable-end(reentrancy-events)
    }

    function _validatePolicy(address[] calldata approvers, uint16 threshold) private pure {
        if (approvers.length == 0 || approvers.length > MAX_APPROVERS) revert InvalidPolicy();
        if (threshold == 0 || threshold > approvers.length) revert InvalidPolicy();
        for (uint256 i; i < approvers.length; ++i) {
            // The loop is explicitly bounded to MAX_APPROVERS (16).
            // forge-lint: disable-next-line(require-revert-in-loop)
            if (approvers[i] == address(0)) revert ZeroAddress();
            for (uint256 j; j < i; ++j) {
                // The nested loop is explicitly bounded to MAX_APPROVERS (16).
                // forge-lint: disable-next-line(require-revert-in-loop)
                if (approvers[i] == approvers[j]) revert InvalidPolicy();
            }
        }
    }

    function _foundationProcurement(bytes32 procurementId)
        private
        view
        returns (ProcurementView storage procurement)
    {
        procurement = _requireProcurement(procurementId);
        if (msg.sender != _projects[procurement.projectId].foundation) {
            revert Unauthorized(msg.sender);
        }
    }

    function _requireProject(bytes32 projectId) private view returns (ProjectView storage project) {
        if (!_projectExists[projectId]) revert UnknownProject(projectId);
        return _projects[projectId];
    }

    function _requireProcurement(bytes32 procurementId)
        private
        view
        returns (ProcurementView storage procurement)
    {
        if (!_procurementExists[procurementId]) revert UnknownProcurement(procurementId);
        return _procurements[procurementId];
    }

    function _expect(ProcurementView storage procurement, ProcurementState state) private view {
        if (procurement.state != state) revert WrongState(uint8(state), uint8(procurement.state));
    }

    function _transition(ProcurementView storage procurement, ProcurementState state) private {
        ProcurementState previous = procurement.state;
        procurement.state = state;
        // State is committed first; signature checks are static and counterpart callbacks are exact.
        // forge-lint: disable-next-line(reentrancy-events)
        emit ProcurementStateChanged(procurement.procurementId, previous, state);
    }

    function _resolve(ProcurementView storage procurement, ProcurementState state) private {
        ProjectView storage project = _projects[procurement.projectId];
        project.unresolvedProcurements--;
        _transition(procurement, state);
    }
}
