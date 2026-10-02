// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { ERC20 } from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @title Mock Hong Kong Dollar
/// @notice TEST-ONLY token with no value, backing, redemption, or production use.
/// @dev Anyone may mint demo balances. Never represent this token as real money.
contract MockHKD is ERC20 {
    error ZeroRecipient();
    error ZeroAmount();

    constructor() ERC20("Mock Hong Kong Dollar", "mHKD") { }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    /// @notice Mints valueless demo tokens for booth personas.
    function mint(address recipient, uint256 amount) external {
        if (recipient == address(0)) revert ZeroRecipient();
        if (amount == 0) revert ZeroAmount();
        _mint(recipient, amount);
    }
}

