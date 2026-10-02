// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { ReentrancyGuard } from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import { EIP712 } from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import { SignatureChecker } from "@openzeppelin/contracts/utils/cryptography/SignatureChecker.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";

import { IPoGRegistryV2 } from "./interfaces/IPoGRegistryV2.sol";
import { IProcurementEscrowV2 } from "./interfaces/IProcurementEscrowV2.sol";

/// @title Procurement Escrow V2
/// @notice Project-scoped donor custody, threshold approvals, and proportional refunds.
contract ProcurementEscrowV2 is IProcurementEscrowV2, EIP712, ReentrancyGuard {
    using SafeERC20 for IERC20;

    struct HumanIntent {
        bytes32 targetId;
        HumanAction action;
        bytes32 termsHash;
        bytes32 assessmentId;
        address signer;
        uint256 nonce;
        uint64 deadline;
        uint32 policyEpoch;
    }

    struct LocalProcurement {
        uint256 reservedAmount;
        uint256 releasedAmount;
        uint256 returnedAmount;
    }

    error ZeroAddress();
    error ZeroAmount();
    error Unauthorized(address caller);
    error RegistryPaused();
    error UnknownProject(bytes32 projectId);
    error DuplicateProject(bytes32 projectId);
    error InvalidPolicy();
    error InvalidEpoch(uint32 expected, uint32 actual);
    error InvalidAction();
    error InvalidTerms(bytes32 expected, bytes32 actual);
    error InvalidAssessment(bytes32 assessmentId);
    error InvalidSignature();
    error SignatureExpired(uint64 deadline);
    error NonceMismatch(address signer, uint256 expected, uint256 actual);
    error DuplicateLiveVote(address signer);
    error InsufficientApprovals(uint256 actual, uint256 required);
    error InvalidState(uint8 expected, uint8 actual);
    error InvalidAmount(uint256 amount);
    error InsufficientFreeLocked(uint256 available, uint256 required);
    error TooManyDonors();
    error TokenBalanceMismatch(uint256 expected, uint256 actual);
    error AlreadyClaimed(address donor);
    error NotDonor(address donor);
    error UnresolvedProcurements(uint32 count);
    error ReturnedFundsBlockSettlement(uint256 returnedAmount);

    event ProjectRegistered(
        bytes32 indexed projectId, address indexed asset, uint32 policyEpoch, uint16 threshold
    );
    event PolicyUpdated(bytes32 indexed projectId, uint32 policyEpoch, uint16 threshold);
    event Donated(
        bytes32 indexed projectId, address indexed donor, uint256 amount, uint256 cumulativeCredit
    );
    event HumanApprovalSubmitted(
        bytes32 indexed targetId,
        HumanAction indexed action,
        bytes32 indexed bundleKey,
        address signer,
        uint64 deadline
    );
    event BudgetReserved(bytes32 indexed procurementId, bytes32 indexed projectId, uint256 amount);
    event FundsReleasedToFoundation(
        bytes32 indexed procurementId,
        bytes32 indexed projectId,
        address indexed foundation,
        uint256 invoiceAmount,
        uint256 unusedReservation
    );
    event MockPaymentConfirmed(
        bytes32 indexed procurementId, bytes32 indexed projectId, uint256 invoiceAmount
    );
    event ReservedProcurementCancelled(
        bytes32 indexed procurementId,
        bytes32 indexed projectId,
        bytes32 reasonHash,
        uint256 releasedReservation
    );
    event ReleasedFundsReturned(
        bytes32 indexed procurementId,
        bytes32 indexed projectId,
        uint256 amount,
        uint256 cumulativeReturned
    );
    event RefundSnapshot(bytes32 indexed projectId, uint256 refundPool, uint256 deposits);
    event RefundClaimed(bytes32 indexed projectId, address indexed donor, uint256 amount);

    uint32 public constant MAX_DONORS = 64;
    uint16 public constant MAX_APPROVERS = 16;

    bytes32 public constant HUMAN_INTENT_TYPEHASH = keccak256(
        "HumanIntent(bytes32 targetId,uint8 action,bytes32 termsHash,bytes32 assessmentId,address signer,uint256 nonce,uint64 deadline,uint32 policyEpoch)"
    );
    bytes32 public constant RESERVE_TERMS_DOMAIN = keccak256("POG_V2_RESERVE_TERMS");
    bytes32 public constant RELEASE_TERMS_DOMAIN = keccak256("POG_V2_RELEASE_TERMS");
    bytes32 public constant SETTLEMENT_TERMS_DOMAIN = keccak256("POG_V2_SETTLEMENT_TERMS");
    bytes32 public constant CANCEL_TERMS_DOMAIN = keccak256("POG_V2_CANCEL_TERMS");
    bytes32 public constant CLOSE_TERMS_DOMAIN = keccak256("POG_V2_CLOSE_TERMS");

    address public immutable override registry;
    bool private _busy;

    mapping(bytes32 => LedgerView) private _ledgers;
    mapping(bytes32 => bool) private _projectExists;
    mapping(bytes32 => address[]) private _approvers;
    mapping(bytes32 => mapping(address => bool)) public isApprover;
    mapping(address => uint256) public humanNonces;
    mapping(bytes32 => mapping(address => uint64)) public voteDeadline;
    mapping(bytes32 => LocalProcurement) private _localProcurements;
    mapping(bytes32 => address[]) private _donors;
    mapping(bytes32 => mapping(address => uint256)) public donorCredit;
    mapping(bytes32 => mapping(address => uint256)) public refundEntitlement;
    mapping(bytes32 => mapping(address => bool)) public refundClaimed;
    mapping(address => uint256) public totalAssetLiability;

    modifier onlyRegistry() {
        if (msg.sender != registry) revert Unauthorized(msg.sender);
        _;
    }

    modifier whenRegistryNotPaused() {
        if (IPoGRegistryV2(registry).paused()) revert RegistryPaused();
        _;
    }

    constructor(address registry_) EIP712("ProcurementEscrowV2", "2") {
        if (registry_ == address(0) || registry_.code.length == 0) revert ZeroAddress();
        registry = registry_;
    }

    function busy() external view override returns (bool) {
        return _busy;
    }

    function registerProjectFromRegistry(
        bytes32 projectId,
        address asset,
        address[] calldata approvers,
        uint16 threshold,
        uint32 epoch
    ) external override onlyRegistry {
        if (_projectExists[projectId]) revert DuplicateProject(projectId);
        _validatePolicy(approvers, threshold);
        _projectExists[projectId] = true;
        LedgerView storage ledger = _ledgers[projectId];
        ledger.asset = asset;
        ledger.policyEpoch = epoch;
        ledger.threshold = threshold;
        _setApprovers(projectId, approvers);
        emit ProjectRegistered(projectId, asset, epoch, threshold);
    }

    function updatePolicyFromRegistry(
        bytes32 projectId,
        address[] calldata approvers,
        uint16 threshold,
        uint32 epoch
    ) external override onlyRegistry {
        LedgerView storage ledger = _requireLedger(projectId);
        if (epoch != ledger.policyEpoch + 1) revert InvalidEpoch(ledger.policyEpoch + 1, epoch);
        _validatePolicy(approvers, threshold);
        address[] storage oldApprovers = _approvers[projectId];
        for (uint256 i; i < oldApprovers.length; ++i) {
            isApprover[projectId][oldApprovers[i]] = false;
        }
        delete _approvers[projectId];
        _setApprovers(projectId, approvers);
        ledger.policyEpoch = epoch;
        ledger.threshold = threshold;
        emit PolicyUpdated(projectId, epoch, threshold);
    }

    function cancelUnreservedFromRegistry(bytes32 procurementId)
        external
        view
        override
        onlyRegistry
    {
        LocalProcurement storage local = _localProcurements[procurementId];
        if (local.reservedAmount != 0 || local.releasedAmount != 0) revert InvalidAmount(0);
    }

    function deposit(bytes32 projectId, uint256 amount)
        external
        nonReentrant
        whenRegistryNotPaused
    {
        if (amount == 0) revert ZeroAmount();
        LedgerView storage ledger = _requireLedger(projectId);
        _requireSolvent(ledger.asset);
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(projectId);
        if (project.state != IPoGRegistryV2.ProjectState.Active) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Active), uint8(project.state));
        }
        if (donorCredit[projectId][msg.sender] == 0) {
            if (ledger.donorCount >= MAX_DONORS) revert TooManyDonors();
            _donors[projectId].push(msg.sender);
            ledger.donorCount++;
        }
        IERC20 asset = IERC20(ledger.asset);
        uint256 beforeBalance = asset.balanceOf(address(this));
        _busy = true;
        asset.safeTransferFrom(msg.sender, address(this), amount);
        _busy = false;
        uint256 received = asset.balanceOf(address(this)) - beforeBalance;
        if (received != amount) revert TokenBalanceMismatch(amount, received);
        donorCredit[projectId][msg.sender] += amount;
        ledger.deposits += amount;
        totalAssetLiability[ledger.asset] += amount;
        _requireSolvent(ledger.asset);
        emit Donated(projectId, msg.sender, amount, donorCredit[projectId][msg.sender]);
    }

    function submitReserveApproval(
        bytes32 procurementId,
        uint256 reserveAmount,
        HumanIntent calldata intent,
        bytes calldata signature
    ) external nonReentrant whenRegistryNotPaused {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (
            procurement.state != IPoGRegistryV2.ProcurementState.PreAssessed
                && procurement.state != IPoGRegistryV2.ProcurementState.ReserveApprovalPending
        ) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.PreAssessed), uint8(procurement.state)
            );
        }
        IPoGRegistryV2.ProjectView memory project =
            IPoGRegistryV2(registry).getProject(procurement.projectId);
        if (project.state != IPoGRegistryV2.ProjectState.Active) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Active), uint8(project.state));
        }
        if (reserveAmount == 0 || reserveAmount > procurement.budgetCap) {
            revert InvalidAmount(reserveAmount);
        }
        _requireCurrentAssessment(
            procurement.preAssessmentId, procurementId, procurement.preEvidenceHash
        );
        bytes32 termsHash = reserveTermsHash(procurementId, reserveAmount);
        _submitIntent(
            procurement.projectId,
            procurementId,
            HumanAction.Reserve,
            termsHash,
            procurement.preAssessmentId,
            intent,
            signature
        );
        if (procurement.state == IPoGRegistryV2.ProcurementState.PreAssessed) {
            IPoGRegistryV2(registry).markReservePendingFromEscrow(procurementId);
        }
    }

    function executeReserve(bytes32 procurementId, uint256 reserveAmount)
        external
        nonReentrant
        whenRegistryNotPaused
    {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (procurement.state != IPoGRegistryV2.ProcurementState.ReserveApprovalPending) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.ReserveApprovalPending),
                uint8(procurement.state)
            );
        }
        IPoGRegistryV2.ProjectView memory project =
            IPoGRegistryV2(registry).getProject(procurement.projectId);
        if (project.state != IPoGRegistryV2.ProjectState.Active) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Active), uint8(project.state));
        }
        if (reserveAmount == 0 || reserveAmount > procurement.budgetCap) {
            revert InvalidAmount(reserveAmount);
        }
        _requireCurrentAssessment(
            procurement.preAssessmentId, procurementId, procurement.preEvidenceHash
        );
        bytes32 termsHash = reserveTermsHash(procurementId, reserveAmount);
        _requireThreshold(
            procurement.projectId,
            procurementId,
            HumanAction.Reserve,
            termsHash,
            procurement.preAssessmentId
        );
        LedgerView storage ledger = _ledgers[procurement.projectId];
        _requireSolvent(ledger.asset);
        uint256 available = _freeLocked(ledger);
        if (available < reserveAmount) revert InsufficientFreeLocked(available, reserveAmount);
        LocalProcurement storage local = _localProcurements[procurementId];
        local.reservedAmount = reserveAmount;
        ledger.reserved += reserveAmount;
        IPoGRegistryV2(registry).markReservedFromEscrow(procurementId, reserveAmount);
        // Registry callback is counterpart-only and this entry point is non-reentrant.
        // forge-lint: disable-next-line(reentrancy-events)
        emit BudgetReserved(procurementId, procurement.projectId, reserveAmount);
    }

    function submitReleaseApproval(
        bytes32 procurementId,
        HumanIntent calldata intent,
        bytes calldata signature
    ) external nonReentrant whenRegistryNotPaused {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (
            procurement.state != IPoGRegistryV2.ProcurementState.FinalAssessed
                && procurement.state != IPoGRegistryV2.ProcurementState.ReleaseApprovalPending
        ) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.FinalAssessed), uint8(procurement.state)
            );
        }
        _requireCurrentAssessment(
            procurement.finalAssessmentId, procurementId, procurement.finalEvidenceHash
        );
        bytes32 termsHash = releaseTermsHash(procurementId);
        _submitIntent(
            procurement.projectId,
            procurementId,
            HumanAction.ReleaseToFoundation,
            termsHash,
            procurement.finalAssessmentId,
            intent,
            signature
        );
        if (procurement.state == IPoGRegistryV2.ProcurementState.FinalAssessed) {
            IPoGRegistryV2(registry).markReleasePendingFromEscrow(procurementId);
        }
    }

    function executeRelease(bytes32 procurementId) external nonReentrant whenRegistryNotPaused {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (procurement.state != IPoGRegistryV2.ProcurementState.ReleaseApprovalPending) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.ReleaseApprovalPending),
                uint8(procurement.state)
            );
        }
        _requireCurrentAssessment(
            procurement.finalAssessmentId, procurementId, procurement.finalEvidenceHash
        );
        bytes32 termsHash = releaseTermsHash(procurementId);
        _requireThreshold(
            procurement.projectId,
            procurementId,
            HumanAction.ReleaseToFoundation,
            termsHash,
            procurement.finalAssessmentId
        );
        LocalProcurement storage local = _localProcurements[procurementId];
        if (local.reservedAmount != procurement.reservedAmount || local.releasedAmount != 0) {
            revert InvalidAmount(local.reservedAmount);
        }
        LedgerView storage ledger = _ledgers[procurement.projectId];
        _requireSolvent(ledger.asset);
        ledger.reserved -= local.reservedAmount;
        ledger.released += procurement.invoiceAmount;
        local.releasedAmount = procurement.invoiceAmount;
        IPoGRegistryV2.ProjectView memory project =
            IPoGRegistryV2(registry).getProject(procurement.projectId);
        totalAssetLiability[ledger.asset] -= procurement.invoiceAmount;
        _transferExact(ledger.asset, project.foundation, procurement.invoiceAmount);
        _requireSolvent(ledger.asset);
        IPoGRegistryV2(registry).markFundsReleasedFromEscrow(procurementId);
        // Token and Registry calls completed atomically under nonReentrant before this event.
        // forge-lint: disable-start(reentrancy-events)
        emit FundsReleasedToFoundation(
            procurementId,
            procurement.projectId,
            project.foundation,
            procurement.invoiceAmount,
            local.reservedAmount - procurement.invoiceAmount
        );
        // forge-lint: disable-end(reentrancy-events)
    }

    function submitSettlementApproval(
        bytes32 procurementId,
        HumanIntent calldata intent,
        bytes calldata signature
    ) external nonReentrant whenRegistryNotPaused {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (
            procurement.state != IPoGRegistryV2.ProcurementState.SettlementRecorded
                && procurement.state != IPoGRegistryV2.ProcurementState.SettlementApprovalPending
        ) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.SettlementRecorded), uint8(procurement.state)
            );
        }
        if (procurement.returnedAmount != 0) {
            revert ReturnedFundsBlockSettlement(procurement.returnedAmount);
        }
        bytes32 termsHash = settlementTermsHash(procurementId);
        _submitIntent(
            procurement.projectId,
            procurementId,
            HumanAction.ConfirmMockPayment,
            termsHash,
            bytes32(0),
            intent,
            signature
        );
        if (procurement.state == IPoGRegistryV2.ProcurementState.SettlementRecorded) {
            IPoGRegistryV2(registry).markSettlementPendingFromEscrow(procurementId);
        }
    }

    function executeSettlementConfirmation(bytes32 procurementId)
        external
        nonReentrant
        whenRegistryNotPaused
    {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (procurement.state != IPoGRegistryV2.ProcurementState.SettlementApprovalPending) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.SettlementApprovalPending),
                uint8(procurement.state)
            );
        }
        if (procurement.returnedAmount != 0) {
            revert ReturnedFundsBlockSettlement(procurement.returnedAmount);
        }
        bytes32 termsHash = settlementTermsHash(procurementId);
        _requireThreshold(
            procurement.projectId,
            procurementId,
            HumanAction.ConfirmMockPayment,
            termsHash,
            bytes32(0)
        );
        IPoGRegistryV2(registry).markPaymentConfirmedFromEscrow(procurementId);
        // Registry callback is counterpart-only and this entry point is non-reentrant.
        // forge-lint: disable-next-line(reentrancy-events)
        emit MockPaymentConfirmed(procurementId, procurement.projectId, procurement.invoiceAmount);
    }

    function requestReservedCancellation(bytes32 procurementId, bytes32 reasonHash)
        external
        nonReentrant
    {
        if (reasonHash == bytes32(0)) revert InvalidTerms(bytes32(0), reasonHash);
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        IPoGRegistryV2.ProjectView memory project =
            IPoGRegistryV2(registry).getProject(procurement.projectId);
        if (msg.sender != project.foundation) revert Unauthorized(msg.sender);
        if (
            procurement.state != IPoGRegistryV2.ProcurementState.Reserved
                && procurement.state != IPoGRegistryV2.ProcurementState.InvoiceRecorded
        ) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.Reserved), uint8(procurement.state)
            );
        }
        IPoGRegistryV2(registry).markCancellationPendingFromEscrow(procurementId, reasonHash);
    }

    function submitCancellationApproval(
        bytes32 procurementId,
        HumanIntent calldata intent,
        bytes calldata signature
    ) external nonReentrant {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (procurement.state != IPoGRegistryV2.ProcurementState.CancellationApprovalPending) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.CancellationApprovalPending),
                uint8(procurement.state)
            );
        }
        bytes32 termsHash = cancellationTermsHash(procurementId);
        _submitIntent(
            procurement.projectId,
            procurementId,
            HumanAction.CancelReserved,
            termsHash,
            bytes32(0),
            intent,
            signature
        );
    }

    function executeReservedCancellation(bytes32 procurementId) external nonReentrant {
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        if (procurement.state != IPoGRegistryV2.ProcurementState.CancellationApprovalPending) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.CancellationApprovalPending),
                uint8(procurement.state)
            );
        }
        bytes32 termsHash = cancellationTermsHash(procurementId);
        _requireThreshold(
            procurement.projectId, procurementId, HumanAction.CancelReserved, termsHash, bytes32(0)
        );
        LocalProcurement storage local = _localProcurements[procurementId];
        LedgerView storage ledger = _ledgers[procurement.projectId];
        ledger.reserved -= local.reservedAmount;
        IPoGRegistryV2(registry).markCancelledFromEscrow(procurementId);
        // Registry callback is counterpart-only and this entry point is non-reentrant.
        // forge-lint: disable-start(reentrancy-events)
        emit ReservedProcurementCancelled(
            procurementId,
            procurement.projectId,
            procurement.cancellationReasonHash,
            local.reservedAmount
        );
        // forge-lint: disable-end(reentrancy-events)
    }

    function returnReleasedFunds(bytes32 procurementId, uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        IPoGRegistryV2.EscrowProcurementView memory procurement =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        IPoGRegistryV2.ProjectView memory project =
            IPoGRegistryV2(registry).getProject(procurement.projectId);
        if (msg.sender != project.foundation) revert Unauthorized(msg.sender);
        if (project.state != IPoGRegistryV2.ProjectState.Closing) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Closing), uint8(project.state));
        }
        if (
            procurement.state != IPoGRegistryV2.ProcurementState.FundsReleased
                && procurement.state != IPoGRegistryV2.ProcurementState.SettlementRecorded
                && procurement.state != IPoGRegistryV2.ProcurementState.SettlementApprovalPending
        ) {
            revert InvalidState(
                uint8(IPoGRegistryV2.ProcurementState.FundsReleased), uint8(procurement.state)
            );
        }
        LocalProcurement storage local = _localProcurements[procurementId];
        if (amount > local.releasedAmount - local.returnedAmount) revert InvalidAmount(amount);
        LedgerView storage ledger = _ledgers[procurement.projectId];
        _requireSolvent(ledger.asset);
        _receiveExact(ledger.asset, msg.sender, amount);
        local.returnedAmount += amount;
        ledger.returned += amount;
        totalAssetLiability[ledger.asset] += amount;
        _requireSolvent(ledger.asset);
        IPoGRegistryV2(registry).noteReturnedFundsFromEscrow(procurementId, local.returnedAmount);
        // Token and Registry calls completed atomically under nonReentrant before this event.
        // forge-lint: disable-start(reentrancy-events)
        emit ReleasedFundsReturned(
            procurementId, procurement.projectId, amount, local.returnedAmount
        );
        // forge-lint: disable-end(reentrancy-events)
    }

    function submitCloseApproval(
        bytes32 projectId,
        HumanIntent calldata intent,
        bytes calldata signature
    ) external nonReentrant whenRegistryNotPaused {
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(projectId);
        if (project.state != IPoGRegistryV2.ProjectState.Closing) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Closing), uint8(project.state));
        }
        if (project.unresolvedProcurements != 0) {
            revert UnresolvedProcurements(project.unresolvedProcurements);
        }
        LedgerView storage ledger = _ledgers[projectId];
        if (ledger.reserved != 0) revert InvalidAmount(ledger.reserved);
        _requireSolvent(ledger.asset);
        bytes32 termsHash = closeTermsHash(projectId);
        _submitIntent(
            projectId, projectId, HumanAction.CloseProject, termsHash, bytes32(0), intent, signature
        );
    }

    function executeClose(bytes32 projectId) external nonReentrant whenRegistryNotPaused {
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(projectId);
        if (project.state != IPoGRegistryV2.ProjectState.Closing) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Closing), uint8(project.state));
        }
        if (project.unresolvedProcurements != 0) {
            revert UnresolvedProcurements(project.unresolvedProcurements);
        }
        LedgerView storage ledger = _ledgers[projectId];
        _requireSolvent(ledger.asset);
        if (ledger.reserved != 0) revert InvalidAmount(ledger.reserved);
        bytes32 termsHash = closeTermsHash(projectId);
        _requireThreshold(projectId, projectId, HumanAction.CloseProject, termsHash, bytes32(0));
        uint256 refundPool = ledger.deposits - (ledger.released - ledger.returned) - ledger.refunded;
        ledger.refundPool = refundPool;
        ledger.refundSnapshotted = true;
        if (refundPool == 0) {
            IPoGRegistryV2(registry).markClosedFromEscrow(projectId);
        } else {
            uint256 cumulative = 0;
            address[] storage donors = _donors[projectId];
            for (uint256 i; i < donors.length; ++i) {
                address donor = donors[i];
                uint256 start = cumulative;
                cumulative += donorCredit[projectId][donor];
                refundEntitlement[projectId][donor] = Math.mulDiv(
                    refundPool, cumulative, ledger.deposits
                ) - Math.mulDiv(refundPool, start, ledger.deposits);
            }
            IPoGRegistryV2(registry).markRefundableFromEscrow(projectId);
        }
        // Registry callback is counterpart-only and this entry point is non-reentrant.
        // forge-lint: disable-next-line(reentrancy-events)
        emit RefundSnapshot(projectId, refundPool, ledger.deposits);
    }

    function claimRefund(bytes32 projectId) external nonReentrant {
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(projectId);
        if (project.state != IPoGRegistryV2.ProjectState.Refundable) {
            revert InvalidState(uint8(IPoGRegistryV2.ProjectState.Refundable), uint8(project.state));
        }
        if (donorCredit[projectId][msg.sender] == 0) revert NotDonor(msg.sender);
        if (refundClaimed[projectId][msg.sender]) revert AlreadyClaimed(msg.sender);
        LedgerView storage ledger = _ledgers[projectId];
        uint256 amount = refundEntitlement[projectId][msg.sender];
        refundClaimed[projectId][msg.sender] = true;
        ledger.claimedCount++;
        ledger.refunded += amount;
        totalAssetLiability[ledger.asset] -= amount;
        if (amount != 0) _transferExact(ledger.asset, msg.sender, amount);
        _requireSolvent(ledger.asset);
        emit RefundClaimed(projectId, msg.sender, amount);
        if (ledger.claimedCount == ledger.donorCount) {
            IPoGRegistryV2(registry).markClosedFromEscrow(projectId);
        }
    }

    function reserveTermsHash(bytes32 procurementId, uint256 reserveAmount)
        public
        view
        returns (bytes32)
    {
        IPoGRegistryV2.EscrowProcurementView memory p =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(p.projectId);
        bytes32 partiesHash = _partiesHash(project, p.vendor);
        return keccak256(
            abi.encode(
                RESERVE_TERMS_DOMAIN,
                partiesHash,
                procurementId,
                p.budgetCap,
                reserveAmount,
                p.preEvidenceHash,
                p.preAssessmentId
            )
        );
    }

    function releaseTermsHash(bytes32 procurementId) public view returns (bytes32) {
        IPoGRegistryV2.EscrowProcurementView memory p =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(p.projectId);
        bytes32 partiesHash = _partiesHash(project, p.vendor);
        return keccak256(
            abi.encode(
                RELEASE_TERMS_DOMAIN,
                partiesHash,
                procurementId,
                p.reservedAmount,
                p.invoiceAmount,
                p.finalEvidenceHash,
                p.finalAssessmentId
            )
        );
    }

    function settlementTermsHash(bytes32 procurementId) public view returns (bytes32) {
        IPoGRegistryV2.EscrowProcurementView memory p =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(p.projectId);
        bytes32 partiesHash = _partiesHash(project, p.vendor);
        return keccak256(
            abi.encode(
                SETTLEMENT_TERMS_DOMAIN,
                partiesHash,
                procurementId,
                p.invoiceAmount,
                p.settlementHash
            )
        );
    }

    function cancellationTermsHash(bytes32 procurementId) public view returns (bytes32) {
        IPoGRegistryV2.EscrowProcurementView memory p =
            IPoGRegistryV2(registry).getEscrowProcurement(procurementId);
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(p.projectId);
        bytes32 partiesHash = _partiesHash(project, p.vendor);
        return keccak256(
            abi.encode(
                CANCEL_TERMS_DOMAIN,
                partiesHash,
                procurementId,
                p.reservedAmount,
                p.cancellationEvidenceHash,
                p.cancellationReasonHash
            )
        );
    }

    function closeTermsHash(bytes32 projectId) public view returns (bytes32) {
        IPoGRegistryV2.ProjectView memory project = IPoGRegistryV2(registry).getProject(projectId);
        LedgerView storage ledger = _requireLedger(projectId);
        uint256 refundPool = ledger.deposits - (ledger.released - ledger.returned) - ledger.refunded;
        bytes32 ledgerHash = keccak256(
            abi.encode(
                ledger.deposits,
                ledger.returned,
                ledger.released,
                ledger.refunded,
                ledger.reserved,
                refundPool,
                ledger.donorCount
            )
        );
        return keccak256(
            abi.encode(
                CLOSE_TERMS_DOMAIN,
                projectId,
                project.foundation,
                project.recipient,
                project.asset,
                project.state,
                project.unresolvedProcurements,
                ledgerHash,
                ledger.policyEpoch
            )
        );
    }

    function intentDigest(HumanIntent calldata intent) external view returns (bytes32) {
        return _hashTypedDataV4(_intentStructHash(intent));
    }

    function getLedger(bytes32 projectId) external view override returns (LedgerView memory) {
        return _requireLedger(projectId);
    }

    function freeLocked(bytes32 projectId) external view returns (uint256) {
        return _freeLocked(_requireLedger(projectId));
    }

    function _submitIntent(
        bytes32 projectId,
        bytes32 targetId,
        HumanAction action,
        bytes32 termsHash,
        bytes32 assessmentId,
        HumanIntent calldata intent,
        bytes calldata signature
    ) private {
        LedgerView storage ledger = _requireLedger(projectId);
        if (intent.targetId != targetId || intent.action != action) revert InvalidAction();
        if (intent.termsHash != termsHash) revert InvalidTerms(termsHash, intent.termsHash);
        if (intent.assessmentId != assessmentId) revert InvalidAssessment(intent.assessmentId);
        if (intent.policyEpoch != ledger.policyEpoch) {
            revert InvalidEpoch(ledger.policyEpoch, intent.policyEpoch);
        }
        if (!isApprover[projectId][intent.signer]) revert Unauthorized(intent.signer);
        // Deadline checks are the intended use of timestamp; small validator drift is harmless.
        // forge-lint: disable-next-line(block-timestamp)
        if (intent.deadline == 0 || block.timestamp > intent.deadline) {
            revert SignatureExpired(intent.deadline);
        }
        uint256 expectedNonce = humanNonces[intent.signer];
        if (intent.nonce != expectedNonce) {
            revert NonceMismatch(intent.signer, expectedNonce, intent.nonce);
        }
        bytes32 bundleKey =
            _bundleKey(targetId, action, termsHash, assessmentId, ledger.policyEpoch);
        uint64 existingDeadline = voteDeadline[bundleKey][intent.signer];
        // The boundary intentionally keeps a vote live through its signed deadline.
        // forge-lint: disable-next-line(block-timestamp)
        if (existingDeadline != 0 && existingDeadline >= block.timestamp) {
            revert DuplicateLiveVote(intent.signer);
        }
        bytes32 digest = _hashTypedDataV4(_intentStructHash(intent));
        if (!SignatureChecker.isValidSignatureNow(intent.signer, digest, signature)) {
            revert InvalidSignature();
        }
        humanNonces[intent.signer] = expectedNonce + 1;
        voteDeadline[bundleKey][intent.signer] = intent.deadline;
        // ERC1271 is validated only at submission and this function is non-reentrant.
        // forge-lint: disable-next-line(reentrancy-events)
        emit HumanApprovalSubmitted(targetId, action, bundleKey, intent.signer, intent.deadline);
    }

    function _requireThreshold(
        bytes32 projectId,
        bytes32 targetId,
        HumanAction action,
        bytes32 termsHash,
        bytes32 assessmentId
    ) private view {
        LedgerView storage ledger = _requireLedger(projectId);
        bytes32 bundleKey =
            _bundleKey(targetId, action, termsHash, assessmentId, ledger.policyEpoch);
        address[] storage approvers = _approvers[projectId];
        uint256 count = 0;
        for (uint256 i; i < approvers.length; ++i) {
            uint64 deadline = voteDeadline[bundleKey][approvers[i]];
            // The boundary intentionally keeps a vote live through its signed deadline.
            // forge-lint: disable-next-line(block-timestamp)
            if (deadline != 0 && deadline >= block.timestamp) ++count;
        }
        if (count < ledger.threshold) revert InsufficientApprovals(count, ledger.threshold);
    }

    function _requireCurrentAssessment(
        bytes32 assessmentId,
        bytes32 procurementId,
        bytes32 evidenceHash
    ) private view {
        IPoGRegistryV2.AssessmentView memory assessment =
            IPoGRegistryV2(registry).getAssessment(assessmentId);
        if (
            assessment.procurementId != procurementId || assessment.evidenceHash != evidenceHash
                || !IPoGRegistryV2(registry).aiSigners(assessment.signer)
                // Deadline checks are the intended use of timestamp.
                // forge-lint: disable-next-line(block-timestamp)
                || block.timestamp > assessment.deadline
        ) {
            revert InvalidAssessment(assessmentId);
        }
    }

    function _intentStructHash(HumanIntent calldata intent) private pure returns (bytes32) {
        return keccak256(
            abi.encode(
                HUMAN_INTENT_TYPEHASH,
                intent.targetId,
                intent.action,
                intent.termsHash,
                intent.assessmentId,
                intent.signer,
                intent.nonce,
                intent.deadline,
                intent.policyEpoch
            )
        );
    }

    function _bundleKey(
        bytes32 targetId,
        HumanAction action,
        bytes32 termsHash,
        bytes32 assessmentId,
        uint32 epoch
    ) private pure returns (bytes32) {
        return keccak256(abi.encode(targetId, action, termsHash, assessmentId, epoch));
    }

    function _receiveExact(address assetAddress, address from, uint256 amount) private {
        IERC20 asset = IERC20(assetAddress);
        uint256 beforeBalance = asset.balanceOf(address(this));
        _busy = true;
        asset.safeTransferFrom(from, address(this), amount);
        _busy = false;
        uint256 actual = asset.balanceOf(address(this)) - beforeBalance;
        if (actual != amount) revert TokenBalanceMismatch(amount, actual);
    }

    function _transferExact(address assetAddress, address to, uint256 amount) private {
        IERC20 asset = IERC20(assetAddress);
        uint256 escrowBefore = asset.balanceOf(address(this));
        uint256 recipientBefore = asset.balanceOf(to);
        _busy = true;
        asset.safeTransfer(to, amount);
        _busy = false;
        uint256 escrowDelta = escrowBefore - asset.balanceOf(address(this));
        uint256 recipientDelta = asset.balanceOf(to) - recipientBefore;
        if (escrowDelta != amount || recipientDelta != amount) {
            revert TokenBalanceMismatch(amount, recipientDelta);
        }
    }

    function _freeLocked(LedgerView storage ledger) private view returns (uint256) {
        return
            ledger.deposits - (ledger.released - ledger.returned) - ledger.reserved
                - ledger.refunded;
    }

    function _partiesHash(IPoGRegistryV2.ProjectView memory project, address vendor)
        private
        pure
        returns (bytes32)
    {
        return keccak256(
            abi.encode(
                project.projectId, project.foundation, project.recipient, vendor, project.asset
            )
        );
    }

    function _requireSolvent(address asset) private view {
        uint256 actual = IERC20(asset).balanceOf(address(this));
        uint256 required = totalAssetLiability[asset];
        if (actual < required) revert TokenBalanceMismatch(required, actual);
    }

    function _setApprovers(bytes32 projectId, address[] calldata approvers) private {
        for (uint256 i; i < approvers.length; ++i) {
            _approvers[projectId].push(approvers[i]);
            isApprover[projectId][approvers[i]] = true;
        }
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

    function _requireLedger(bytes32 projectId) private view returns (LedgerView storage ledger) {
        if (!_projectExists[projectId]) revert UnknownProject(projectId);
        return _ledgers[projectId];
    }
}
