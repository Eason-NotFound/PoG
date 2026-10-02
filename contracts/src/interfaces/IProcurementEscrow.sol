// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @notice Minimal Registry-facing surface. Custody and payment logic is out of M1 scope.
interface IProcurementEscrow {
    function registry() external view returns (address);

    function registerProjectFromRegistry(
        bytes32 projectId,
        address asset,
        uint8 assetDecimals,
        address[] calldata approvers,
        uint16 threshold,
        uint32 epoch
    ) external;

    function updateApprovalPolicyFromRegistry(
        bytes32 projectId,
        address[] calldata approvers,
        uint16 threshold,
        uint32 newEpoch
    ) external;

    function releaseOnCancellationFromRegistry(bytes32 procurementId)
        external
        returns (uint256 released);
}

