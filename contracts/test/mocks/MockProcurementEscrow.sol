// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { IProcurementEscrow } from "../../src/interfaces/IProcurementEscrow.sol";
import { PoGRegistry } from "../../src/PoGRegistry.sol";

contract MockProcurementEscrow is IProcurementEscrow {
    error HookReverted();
    error RegistryOnly();

    address public immutable registry;

    bool public revertRegister;
    bool public revertPolicy;
    bool public revertRelease;
    bool public attemptReentry;
    bool public reentrySucceeded;

    mapping(bytes32 projectId => bool) public registered;
    mapping(bytes32 projectId => uint32) public epoch;
    mapping(bytes32 projectId => uint16) public threshold;
    mapping(bytes32 procurementId => uint256) public releaseAmount;

    modifier onlyRegistry() {
        if (msg.sender != registry) revert RegistryOnly();
        _;
    }

    constructor(address registry_) {
        registry = registry_;
    }

    function setReverts(bool register_, bool policy_, bool release_) external {
        revertRegister = register_;
        revertPolicy = policy_;
        revertRelease = release_;
    }

    function setAttemptReentry(bool enabled) external {
        attemptReentry = enabled;
    }

    function setReleaseAmount(bytes32 procurementId, uint256 amount) external {
        releaseAmount[procurementId] = amount;
    }

    function registerProjectFromRegistry(
        bytes32 projectId,
        address asset,
        uint8 assetDecimals,
        address[] calldata approvers,
        uint16 threshold_,
        uint32 epoch_
    ) external onlyRegistry {
        if (revertRegister) revert HookReverted();
        registered[projectId] = true;
        epoch[projectId] = epoch_;
        threshold[projectId] = threshold_;

        if (attemptReentry) {
            bytes32 reentryId = keccak256(abi.encodePacked("reentry", projectId));
            (reentrySucceeded,) = registry.call(
                abi.encodeCall(
                    PoGRegistry.createProject,
                    (reentryId, address(0xBEEF), asset, approvers, threshold_)
                )
            );
            assert(assetDecimals == 6);
        }
    }

    function updateApprovalPolicyFromRegistry(
        bytes32 projectId,
        address[] calldata,
        uint16 threshold_,
        uint32 newEpoch
    ) external onlyRegistry {
        if (revertPolicy) revert HookReverted();
        epoch[projectId] = newEpoch;
        threshold[projectId] = threshold_;
    }

    function releaseOnCancellationFromRegistry(bytes32 procurementId)
        external
        view
        onlyRegistry
        returns (uint256 released)
    {
        if (revertRelease) revert HookReverted();
        return releaseAmount[procurementId];
    }

    function markReservePending(bytes32 procurementId) external {
        PoGRegistry(registry).markReserveApprovalPendingFromEscrow(procurementId);
    }

    function markReserved(bytes32 procurementId, uint256 amount) external {
        PoGRegistry(registry).markReservedFromEscrow(procurementId, amount);
    }

    function markPaymentPending(bytes32 procurementId) external {
        PoGRegistry(registry).markPaymentApprovalPendingFromEscrow(procurementId);
    }

    function markPaid(bytes32 procurementId, uint256 amount) external {
        PoGRegistry(registry).markPaidFromEscrow(procurementId, amount);
    }
}
