// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IProcurementEscrowV2 {
    enum HumanAction {
        Reserve,
        ReleaseToFoundation,
        ConfirmMockPayment,
        CancelReserved,
        CloseProject
    }

    struct LedgerView {
        address asset;
        uint256 deposits;
        uint256 reserved;
        uint256 released;
        uint256 returned;
        uint256 refunded;
        uint256 refundPool;
        uint32 donorCount;
        uint32 claimedCount;
        uint32 policyEpoch;
        uint16 threshold;
        bool refundSnapshotted;
    }

    function registry() external view returns (address);
    function busy() external view returns (bool);
    function getLedger(bytes32 projectId) external view returns (LedgerView memory);

    function registerProjectFromRegistry(
        bytes32 projectId,
        address asset,
        address[] calldata approvers,
        uint16 threshold,
        uint32 epoch
    ) external;

    function updatePolicyFromRegistry(
        bytes32 projectId,
        address[] calldata approvers,
        uint16 threshold,
        uint32 epoch
    ) external;

    function cancelUnreservedFromRegistry(bytes32 procurementId) external view;
}
