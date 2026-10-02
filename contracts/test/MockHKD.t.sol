// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { Test } from "forge-std/Test.sol";

import { MockHKD } from "../src/MockHKD.sol";

contract MockHKDTest is Test {
    MockHKD internal token;
    address internal alice = makeAddr("alice");
    address internal bob = makeAddr("bob");
    address internal spender = makeAddr("spender");

    function setUp() public {
        token = new MockHKD();
    }

    function test_Metadata() public view {
        assertEq(token.name(), "Mock Hong Kong Dollar");
        assertEq(token.symbol(), "mHKD");
        assertEq(token.decimals(), 6);
        assertEq(token.totalSupply(), 0);
    }

    function test_PublicMintAndBalance() public {
        vm.prank(alice);
        token.mint(bob, 25_000_000);
        assertEq(token.balanceOf(bob), 25_000_000);
        assertEq(token.totalSupply(), 25_000_000);
    }

    function test_MintRejectsZeroRecipient() public {
        vm.expectRevert(MockHKD.ZeroRecipient.selector);
        token.mint(address(0), 1);
    }

    function test_MintRejectsZeroAmount() public {
        vm.expectRevert(MockHKD.ZeroAmount.selector);
        token.mint(alice, 0);
    }

    function test_TransferApproveAndTransferFrom() public {
        token.mint(alice, 10_000_000);

        vm.prank(alice);
        assertTrue(token.transfer(bob, 2_000_000));
        assertEq(token.balanceOf(alice), 8_000_000);
        assertEq(token.balanceOf(bob), 2_000_000);

        vm.prank(alice);
        assertTrue(token.approve(spender, 3_000_000));
        assertEq(token.allowance(alice, spender), 3_000_000);

        vm.prank(spender);
        assertTrue(token.transferFrom(alice, bob, 1_500_000));
        assertEq(token.balanceOf(alice), 6_500_000);
        assertEq(token.balanceOf(bob), 3_500_000);
        assertEq(token.allowance(alice, spender), 1_500_000);
    }

    function test_TransferToZeroReverts() public {
        token.mint(alice, 1);
        vm.prank(alice);
        vm.expectRevert();
        token.transfer(address(0), 1);
    }

    function testFuzz_Mint(uint96 amount) public {
        vm.assume(amount > 0);
        token.mint(alice, amount);
        assertEq(token.balanceOf(alice), amount);
        assertEq(token.totalSupply(), amount);
    }
}

