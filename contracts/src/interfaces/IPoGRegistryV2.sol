// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IPoGRegistryV2 {
    enum ProjectState {
        Active,
        Closing,
        Refundable,
        Closed
    }

    enum ProcurementState {
        Created,
        PORecorded,
        PreAssessed,
        ReserveApprovalPending,
        Reserved,
        InvoiceRecorded,
        ReceiptConfirmed,
        FinalAssessed,
        ReleaseApprovalPending,
        FundsReleased,
        SettlementRecorded,
        SettlementApprovalPending,
        PaymentConfirmed,
        CancellationApprovalPending,
        Cancelled
    }

    enum AssessmentStage {
        PrePurchase,
        FinalRelease
    }

    enum AssessmentOutcome {
        Pass,
        Review,
        Reject
    }

    struct ProjectView {
        bytes32 projectId;
        address foundation;
        address recipient;
        address asset;
        uint8 assetDecimals;
        uint32 policyEpoch;
        uint16 threshold;
        ProjectState state;
        uint32 unresolvedProcurements;
        uint64 createdAt;
    }

    struct ProcurementView {
        bytes32 procurementId;
        bytes32 projectId;
        address vendor;
        uint256 budgetCap;
        bytes32 poHash;
        bytes32 requestHash;
        bytes32 goodsRequestHash;
        bytes32 preEvidenceHash;
        bytes32 preAssessmentId;
        uint256 reservedAmount;
        bytes32 invoiceHash;
        uint256 invoiceAmount;
        bytes32 goodsHash;
        bytes32 receiptDigest;
        bytes32 finalEvidenceHash;
        bytes32 finalAssessmentId;
        bytes32 conversionEvidenceHash;
        bytes32 paymentEvidenceHash;
        bytes32 settlementHash;
        bytes32 cancellationReasonHash;
        uint256 returnedAmount;
        ProcurementState state;
    }

    struct AssessmentView {
        bytes32 assessmentId;
        bytes32 procurementId;
        AssessmentStage stage;
        AssessmentOutcome outcome;
        uint16 riskScoreBps;
        bytes32 evidenceHash;
        bytes32 reportHash;
        address signer;
        uint256 nonce;
        uint64 deadline;
    }

    struct EscrowProcurementView {
        bytes32 procurementId;
        bytes32 projectId;
        address vendor;
        uint256 budgetCap;
        bytes32 preEvidenceHash;
        bytes32 preAssessmentId;
        uint256 reservedAmount;
        uint256 invoiceAmount;
        bytes32 finalEvidenceHash;
        bytes32 finalAssessmentId;
        bytes32 settlementHash;
        bytes32 cancellationEvidenceHash;
        bytes32 cancellationReasonHash;
        uint256 returnedAmount;
        ProcurementState state;
    }

    function escrow() external view returns (address);
    function escrowBusy() external view returns (bool);
    function paused() external view returns (bool);
    function aiSigners(address signer) external view returns (bool);
    function getProject(bytes32 projectId) external view returns (ProjectView memory);
    function getProcurement(bytes32 procurementId) external view returns (ProcurementView memory);
    function getEscrowProcurement(bytes32 procurementId)
        external
        view
        returns (EscrowProcurementView memory);
    function getAssessment(bytes32 assessmentId) external view returns (AssessmentView memory);

    function markReservePendingFromEscrow(bytes32 procurementId) external;
    function markReservedFromEscrow(bytes32 procurementId, uint256 amount) external;
    function markReleasePendingFromEscrow(bytes32 procurementId) external;
    function markFundsReleasedFromEscrow(bytes32 procurementId) external;
    function markSettlementPendingFromEscrow(bytes32 procurementId) external;
    function markPaymentConfirmedFromEscrow(bytes32 procurementId) external;
    function markCancellationPendingFromEscrow(bytes32 procurementId, bytes32 reasonHash) external;
    function markCancelledFromEscrow(bytes32 procurementId) external;
    function noteReturnedFundsFromEscrow(bytes32 procurementId, uint256 cumulativeReturned) external;
    function markRefundableFromEscrow(bytes32 projectId) external;
    function markClosedFromEscrow(bytes32 projectId) external;
}
