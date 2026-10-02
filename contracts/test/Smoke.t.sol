// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @notice Dependency-free test proving the M0 Foundry layout is discoverable.
contract SmokeTest {
    function testSmoke() external pure {
        require(keccak256("PoG-M0") == keccak256("PoG-M0"), "smoke failed");
    }
}

