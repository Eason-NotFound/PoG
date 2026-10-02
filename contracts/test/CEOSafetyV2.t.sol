// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { Test } from "forge-std/Test.sol";
import { ERC20 } from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { MockHKD } from "../src/MockHKD.sol";
import { PoGRegistryV2 } from "../src/PoGRegistryV2.sol";
import { ProcurementEscrowV2 } from "../src/ProcurementEscrowV2.sol";
import { IPoGRegistryV2 } from "../src/interfaces/IPoGRegistryV2.sol";
import { IProcurementEscrowV2 } from "../src/interfaces/IProcurementEscrowV2.sol";

interface CEOMintableV2 {
    function mint(address to, uint256 amount) external;
}

/// @dev Adversarial token exists only in this independently owned test file.
contract CEOAdversarialTokenV2 is ERC20 {
    bool public noOpOutgoing;
    bool public feeOutgoing;
    address public callbackTarget;
    bytes public callbackData;
    bool public callbackSucceeded;
    bytes public callbackResult;

    constructor() ERC20("CEO adversarial test token", "TEST") { }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function burnForTest(address from, uint256 amount) external {
        _burn(from, amount);
    }

    function configure(bool noOp, bool fee, address target, bytes calldata data) external {
        noOpOutgoing = noOp;
        feeOutgoing = fee;
        callbackTarget = target;
        callbackData = data;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (noOpOutgoing) return true;
        if (feeOutgoing) {
            _transfer(msg.sender, to, amount - 1);
            _burn(msg.sender, 1);
        } else {
            _transfer(msg.sender, to, amount);
        }
        if (callbackTarget != address(0)) {
            (callbackSucceeded, callbackResult) = callbackTarget.call(callbackData);
        }
        return true;
    }
}

/// @notice Independent CEO regression tests; no dependency on the coder's fixture.
contract CEOSafetyV2Test is Test {
    uint256 private constant AI_KEY = 0xA11CE;
    uint256 private constant RECIPIENT_KEY = 0xBEEF;
    uint256 private constant HUMAN_KEY = 0xCAFE;
    uint256 private constant HUMAN_TWO_KEY = 0xD00D;
    uint256 private constant HUMAN_THREE_KEY = 0xF00D;
    bytes32 private constant PROJECT = keccak256("CEO project");
    bytes32 private constant PROCUREMENT = keccak256("CEO procurement");
    bytes32 private constant PO = keccak256("CEO PO");
    bytes32 private constant INVOICE_HASH = keccak256("CEO invoice");
    bytes32 private constant GOODS = keccak256("CEO goods");

    PoGRegistryV2 private registry;
    ProcurementEscrowV2 private escrow;
    MockHKD private token;
    address private foundation;
    address private recipient;
    address private donorA;
    address private donorB;

    function setUp() public {
        vm.warp(1_900_000_000);
        vm.chainId(31337);
        foundation = makeAddr("CEO foundation");
        recipient = vm.addr(RECIPIENT_KEY);
        donorA = makeAddr("CEO donor A");
        donorB = makeAddr("CEO donor B");
        registry = new PoGRegistryV2(address(this));
        escrow = new ProcurementEscrowV2(address(registry));
        registry.bindEscrow(address(escrow));
        registry.setAISigner(vm.addr(AI_KEY), true);
        token = new MockHKD();
    }

    function test_CEO_ClosingBlocksNewMoneyAndReserveButSettlesOldObligation() public {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 100_000_000);
        _prepareReserved(PROCUREMENT, PROJECT, 80_000_000);
        bytes32 unreserved = keccak256("CEO unreserved at close");
        _preparePre(unreserved, PROJECT, 100_000_000);
        _reserveVote(unreserved, 10_000_000, HUMAN_KEY, _future());
        vm.prank(foundation);
        registry.requestClosing(PROJECT);

        vm.prank(donorA);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidState.selector);
        escrow.deposit(PROJECT, 1);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistryV2.WrongState.selector);
        registry.createProcurement(keccak256("new"), PROJECT, makeAddr("new vendor"), 1);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidState.selector);
        escrow.executeReserve(unreserved, 10_000_000);

        _invoiceReceipt(PROCUREMENT, 72_000_000, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        assertEq(token.balanceOf(foundation), 72_000_000);
        assertEq(escrow.freeLocked(PROJECT), 28_000_000);
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 2);
        vm.expectPartialRevert(ProcurementEscrowV2.UnresolvedProcurements.selector);
        escrow.executeClose(PROJECT);
        vm.prank(foundation);
        registry.cancelUnreserved(unreserved);
        _settle(PROCUREMENT);
        _close(PROJECT);
        assertEq(escrow.refundEntitlement(PROJECT, donorA), 28_000_000);
        vm.prank(donorA);
        escrow.claimRefund(PROJECT);
        assertEq(token.balanceOf(donorA), 28_000_000);
        assertEq(
            uint8(registry.getProject(PROJECT).state), uint8(IPoGRegistryV2.ProjectState.Closed)
        );
    }

    function test_CEO_ReturnDoesNotEraseDebtOrPermitClosing() public {
        _fundAndRelease(100_000_000, 80_000_000, 72_000_000);
        vm.prank(foundation);
        registry.requestClosing(PROJECT);
        vm.startPrank(foundation);
        token.approve(address(escrow), 72_000_000);
        escrow.returnReleasedFunds(PROCUREMENT, 72_000_000);
        vm.expectPartialRevert(PoGRegistryV2.ReturnedFundsBlockSettlement.selector);
        registry.recordSettlement(PROCUREMENT, keccak256("conversion"), keccak256("payment"));
        vm.stopPrank();
        assertEq(escrow.freeLocked(PROJECT), 100_000_000);
        assertEq(escrow.totalAssetLiability(address(token)), 100_000_000);
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 1);
        vm.expectPartialRevert(ProcurementEscrowV2.UnresolvedProcurements.selector);
        escrow.executeClose(PROJECT);
        assertFalse(escrow.getLedger(PROJECT).refundSnapshotted);
    }

    function test_CEO_ReturnAfterSettlementEvidenceInvalidatesFullConfirmation() public {
        _fundAndRelease(100_000_000, 80_000_000, 72_000_000);
        _recordSettlement(PROCUREMENT);
        _settlementVote(PROCUREMENT, HUMAN_KEY, _future());
        vm.startPrank(foundation);
        registry.requestClosing(PROJECT);
        token.approve(address(escrow), 1);
        escrow.returnReleasedFunds(PROCUREMENT, 1);
        vm.stopPrank();
        vm.expectPartialRevert(ProcurementEscrowV2.ReturnedFundsBlockSettlement.selector);
        escrow.executeSettlementConfirmation(PROCUREMENT);
        vm.expectPartialRevert(ProcurementEscrowV2.UnresolvedProcurements.selector);
        escrow.executeClose(PROJECT);
        assertEq(registry.getProcurement(PROCUREMENT).returnedAmount, 1);
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 1);
    }

    function test_CEO_IncorrectInvoiceCanCancelBeforeRecipientButNotAfter() public {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 100);
        _prepareReserved(PROCUREMENT, PROJECT, 80);
        vm.prank(foundation);
        registry.recordInvoiceAndGoods(PROCUREMENT, INVOICE_HASH, 72, GOODS);
        vm.prank(foundation);
        escrow.requestReservedCancellation(PROCUREMENT, keccak256("invoice correction"));
        _cancellationVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeReservedCancellation(PROCUREMENT);
        assertEq(escrow.freeLocked(PROJECT), 100);
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 0);
        bytes32 other = keccak256("already received");
        _prepareReserved(other, PROJECT, 80);
        _invoiceReceipt(other, 72, _future());
        vm.prank(foundation);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidState.selector);
        escrow.requestReservedCancellation(other, keccak256("erase debt"));
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 1);
    }

    function test_CEO_HumanExpiredRenewalCannotDoubleCountAtTwoOfThree() public {
        _project(PROJECT, address(token), foundation);
        _setThreeOfTwo(PROJECT);
        _deposit(PROJECT, donorA, 100);
        _preparePre(PROCUREMENT, PROJECT, 100);
        uint64 shortDeadline = uint64(vm.getBlockTimestamp() + 2);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, shortDeadline);
        vm.warp(vm.getBlockTimestamp() + 3);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        assertEq(escrow.humanNonces(vm.addr(HUMAN_KEY)), 2);
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        vm.expectPartialRevert(ProcurementEscrowV2.DuplicateLiveVote.selector);
        this.reserveVoteForCEO(PROCUREMENT, 80, HUMAN_KEY, _future());
        assertEq(escrow.humanNonces(vm.addr(HUMAN_KEY)), 2);
        _reserveVote(PROCUREMENT, 80, HUMAN_TWO_KEY, _future());
        escrow.executeReserve(PROCUREMENT, 80);
        assertEq(escrow.getLedger(PROJECT).reserved, 80);
    }

    function test_CEO_AIRenewalInvalidatesOldVotesWithoutRewindingExecutedState() public {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 100);
        _preparePre(PROCUREMENT, PROJECT, 100);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        bytes32 oldAssessment = registry.getProcurement(PROCUREMENT).preAssessmentId;
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.PrePurchase, _future());
        assertNotEq(registry.getProcurement(PROCUREMENT).preAssessmentId, oldAssessment);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        escrow.executeReserve(PROCUREMENT, 80);
        vm.expectPartialRevert(PoGRegistryV2.InvalidAssessmentStage.selector);
        this.assessForCEO(PROCUREMENT, IPoGRegistryV2.AssessmentStage.PrePurchase, _future());
        _invoiceReceipt(PROCUREMENT, 72, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        vm.expectPartialRevert(PoGRegistryV2.InvalidAssessmentStage.selector);
        this.assessForCEO(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _settle(PROCUREMENT);
        vm.expectPartialRevert(PoGRegistryV2.InvalidAssessmentStage.selector);
        this.assessForCEO(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 0);
    }

    function test_CEO_RecipientCannotBeForgedOrMovedAcrossProject() public {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 100);
        _prepareReserved(PROCUREMENT, PROJECT, 80);
        vm.prank(foundation);
        registry.recordInvoiceAndGoods(PROCUREMENT, INVOICE_HASH, 72, GOODS);
        PoGRegistryV2.RecipientReceiptInput memory receipt = _receipt(PROCUREMENT, _future());
        vm.expectPartialRevert(PoGRegistryV2.InvalidSignature.selector);
        registry.submitRecipientReceipt(receipt, _sign(HUMAN_KEY, _receiptDigest(receipt)));
        receipt.projectId = keccak256("different project");
        vm.expectPartialRevert(PoGRegistryV2.InvalidEvidence.selector);
        registry.submitRecipientReceipt(receipt, _sign(RECIPIENT_KEY, _receiptDigest(receipt)));
        assertEq(registry.recipientNonces(recipient), 0);
        receipt = _receipt(PROCUREMENT, uint64(vm.getBlockTimestamp() + 1));
        registry.submitRecipientReceipt(receipt, _sign(RECIPIENT_KEY, _receiptDigest(receipt)));
        vm.warp(vm.getBlockTimestamp() + 2);
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        assertEq(token.balanceOf(foundation), 72);
    }

    function test_CEO_PolicyEpochChangeInvalidatesPendingVotes() public {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 100);
        _preparePre(PROCUREMENT, PROJECT, 100);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        _setThreeOfTwo(PROJECT);
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        _reserveVote(PROCUREMENT, 80, HUMAN_TWO_KEY, _future());
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        escrow.executeReserve(PROCUREMENT, 80);
        assertEq(escrow.getLedger(PROJECT).policyEpoch, 2);
        assertEq(registry.getProject(PROJECT).policyEpoch, 2);
    }

    function test_CEO_AIRenewalMakesBothOldThresholdVotesUnusable() public {
        _project(PROJECT, address(token), foundation);
        _setThreeOfTwo(PROJECT);
        _deposit(PROJECT, donorA, 100);
        _preparePre(PROCUREMENT, PROJECT, 100);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        _reserveVote(PROCUREMENT, 80, HUMAN_TWO_KEY, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.PrePurchase, _future());
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        _reserveVote(PROCUREMENT, 80, HUMAN_TWO_KEY, _future());
        escrow.executeReserve(PROCUREMENT, 80);
        _invoiceReceipt(PROCUREMENT, 72, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        _releaseVote(PROCUREMENT, HUMAN_TWO_KEY, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeRelease(PROCUREMENT);
        assertEq(token.balanceOf(foundation), 0);
        assertEq(escrow.getLedger(PROJECT).reserved, 80);
        _releaseVote(PROCUREMENT, HUMAN_TWO_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        assertEq(token.balanceOf(foundation), 72);
    }

    function test_CEO_ZeroRefundClaimsAndRepeatDepositsHaveNoDustOrSweep() public {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 1);
        _deposit(PROJECT, donorB, 1);
        _deposit(PROJECT, donorA, 98);
        _prepareReserved(PROCUREMENT, PROJECT, 100);
        _invoiceReceipt(PROCUREMENT, 99, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        _settle(PROCUREMENT);
        _close(PROJECT);
        assertEq(escrow.getLedger(PROJECT).donorCount, 2);
        assertEq(escrow.refundEntitlement(PROJECT, donorA), 0);
        assertEq(escrow.refundEntitlement(PROJECT, donorB), 1);
        vm.prank(donorB);
        escrow.claimRefund(PROJECT);
        assertEq(
            uint8(registry.getProject(PROJECT).state), uint8(IPoGRegistryV2.ProjectState.Refundable)
        );
        vm.prank(donorB);
        vm.expectPartialRevert(ProcurementEscrowV2.AlreadyClaimed.selector);
        escrow.claimRefund(PROJECT);
        vm.prank(donorA);
        escrow.claimRefund(PROJECT);
        assertTrue(escrow.refundClaimed(PROJECT, donorA));
        assertEq(escrow.getLedger(PROJECT).claimedCount, 2);
        assertEq(escrow.getLedger(PROJECT).refunded, 1);
        assertEq(token.balanceOf(address(escrow)), 0);
        assertEq(escrow.totalAssetLiability(address(token)), 0);
    }

    function test_CEO_64DonorRefundsUseFixedIntervalsAndExactSum() public {
        _project(PROJECT, address(token), foundation);
        uint256 total;
        for (uint256 i; i < 64; ++i) {
            address donor = address(uint160(10_000 + i));
            uint256 amount = 101 + i;
            total += amount;
            _deposit(PROJECT, donor, amount);
        }
        address first = address(uint160(10_000));
        _deposit(PROJECT, first, 7);
        total += 7;
        address extra = makeAddr("65th donor");
        token.mint(extra, 1);
        vm.prank(extra);
        token.approve(address(escrow), 1);
        vm.prank(extra);
        vm.expectPartialRevert(ProcurementEscrowV2.TooManyDonors.selector);
        escrow.deposit(PROJECT, 1);
        _prepareReserved(PROCUREMENT, PROJECT, total);
        _invoiceReceipt(PROCUREMENT, total - 17, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        _settle(PROCUREMENT);
        _close(PROJECT);
        uint256 cumulative;
        uint256 sum;
        for (uint256 i; i < 64; ++i) {
            address donor = address(uint160(10_000 + i));
            uint256 start = cumulative;
            cumulative += escrow.donorCredit(PROJECT, donor);
            uint256 expected = Math.mulDiv(17, cumulative, total) - Math.mulDiv(17, start, total);
            assertEq(escrow.refundEntitlement(PROJECT, donor), expected);
            sum += expected;
        }
        assertEq(sum, 17);
        for (uint256 i = 64; i > 0; --i) {
            vm.prank(address(uint160(10_000 + i - 1)));
            escrow.claimRefund(PROJECT);
        }
        assertEq(escrow.getLedger(PROJECT).refunded, 17);
        assertEq(escrow.getLedger(PROJECT).claimedCount, 64);
        assertEq(
            uint8(registry.getProject(PROJECT).state), uint8(IPoGRegistryV2.ProjectState.Closed)
        );
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    function test_CEO_SharedAssetOtherProjectAndSurplusCannotBeSpent() public {
        _fundAndRelease(100, 80, 72);
        _settle(PROCUREMENT);
        bytes32 otherProject = keccak256("CEO other project");
        bytes32 otherProcurement = keccak256("CEO other procurement");
        _project(otherProject, address(token), foundation);
        _deposit(otherProject, donorB, 200);
        token.mint(address(escrow), 15);
        _preparePre(otherProcurement, otherProject, 500);
        _reserveVote(otherProcurement, 201, HUMAN_KEY, _future());
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientFreeLocked.selector);
        escrow.executeReserve(otherProcurement, 201);
        _close(PROJECT);
        vm.prank(donorA);
        escrow.claimRefund(PROJECT);
        assertEq(escrow.freeLocked(otherProject), 200);
        assertEq(escrow.totalAssetLiability(address(token)), 200);
        assertEq(token.balanceOf(address(escrow)), 215);
        assertEq(escrow.donorCredit(otherProject, donorA), 0);
    }

    function test_CEO_NoOpOutgoingReleaseRollsBackEverything() public {
        CEOAdversarialTokenV2 adversarial = new CEOAdversarialTokenV2();
        _project(PROJECT, address(adversarial), foundation);
        _deposit(PROJECT, donorA, 100);
        _prepareReserved(PROCUREMENT, PROJECT, 80);
        _invoiceReceipt(PROCUREMENT, 72, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        adversarial.configure(true, false, address(0), "");
        vm.expectPartialRevert(ProcurementEscrowV2.TokenBalanceMismatch.selector);
        escrow.executeRelease(PROCUREMENT);
        assertEq(escrow.getLedger(PROJECT).reserved, 80);
        assertEq(escrow.getLedger(PROJECT).released, 0);
        assertEq(escrow.totalAssetLiability(address(adversarial)), 100);
        assertEq(adversarial.balanceOf(address(escrow)), 100);
        assertEq(adversarial.balanceOf(foundation), 0);
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT).state),
            uint8(IPoGRegistryV2.ProcurementState.ReleaseApprovalPending)
        );
        adversarial.configure(false, false, address(0), "");
        escrow.executeRelease(PROCUREMENT);
        assertEq(adversarial.balanceOf(foundation), 72);
    }

    function test_CEO_FeeOutgoingRefundRollsBackClaimAndLiability() public {
        CEOAdversarialTokenV2 adversarial = new CEOAdversarialTokenV2();
        _project(PROJECT, address(adversarial), foundation);
        _deposit(PROJECT, donorA, 100);
        _close(PROJECT);
        adversarial.configure(false, true, address(0), "");
        vm.prank(donorA);
        vm.expectPartialRevert(ProcurementEscrowV2.TokenBalanceMismatch.selector);
        escrow.claimRefund(PROJECT);
        assertFalse(escrow.refundClaimed(PROJECT, donorA));
        assertEq(escrow.getLedger(PROJECT).claimedCount, 0);
        assertEq(escrow.getLedger(PROJECT).refunded, 0);
        assertEq(escrow.totalAssetLiability(address(adversarial)), 100);
        assertEq(adversarial.balanceOf(address(escrow)), 100);
    }

    function test_CEO_TokenAsFoundationCannotReenterRegistryDuringRelease() public {
        CEOAdversarialTokenV2 adversarial = new CEOAdversarialTokenV2();
        _project(PROJECT, address(adversarial), address(adversarial));
        _deposit(PROJECT, donorA, 100);
        _prepareReserved(PROCUREMENT, PROJECT, 80);
        _invoiceReceipt(PROCUREMENT, 72, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        adversarial.configure(
            false, false, address(registry), abi.encodeCall(PoGRegistryV2.requestClosing, (PROJECT))
        );
        escrow.executeRelease(PROCUREMENT);
        assertFalse(adversarial.callbackSucceeded());
        assertEq(
            bytes4(adversarial.callbackResult()), PoGRegistryV2.EscrowInteractionInProgress.selector
        );
        assertEq(
            uint8(registry.getProject(PROJECT).state), uint8(IPoGRegistryV2.ProjectState.Active)
        );
        assertEq(adversarial.balanceOf(address(adversarial)), 72);
        assertEq(escrow.getLedger(PROJECT).released, 72);
    }

    function test_CEO_TimeZeroMissingVotesNeverMeetThreshold() public {
        vm.warp(0);
        _project(PROJECT, address(token), foundation);
        _setThreeOfTwo(PROJECT);
        _deposit(PROJECT, donorA, 100);
        _preparePre(PROCUREMENT, PROJECT, 100);
        _reserveVote(PROCUREMENT, 80, HUMAN_KEY, _future());
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        assertEq(escrow.getLedger(PROJECT).reserved, 0);
        vm.prank(foundation);
        registry.cancelUnreserved(PROCUREMENT);
        vm.prank(foundation);
        registry.requestClosing(PROJECT);
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            PROJECT,
            PROJECT,
            IProcurementEscrowV2.HumanAction.CloseProject,
            escrow.closeTermsHash(PROJECT),
            bytes32(0),
            HUMAN_KEY,
            _future()
        );
        escrow.submitCloseApproval(PROJECT, intent, _sign(HUMAN_KEY, escrow.intentDigest(intent)));
        vm.expectPartialRevert(ProcurementEscrowV2.InsufficientApprovals.selector);
        escrow.executeClose(PROJECT);
        assertFalse(escrow.getLedger(PROJECT).refundSnapshotted);
    }

    function test_CEO_ZeroDeadlineHumanIntentRejectedEvenAtTimeZero() public {
        vm.warp(0);
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, 100);
        _preparePre(PROCUREMENT, PROJECT, 100);
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            PROCUREMENT,
            PROJECT,
            IProcurementEscrowV2.HumanAction.Reserve,
            escrow.reserveTermsHash(PROCUREMENT, 80),
            registry.getProcurement(PROCUREMENT).preAssessmentId,
            HUMAN_KEY,
            0
        );
        bytes memory signature = _sign(HUMAN_KEY, escrow.intentDigest(intent));
        vm.expectRevert();
        escrow.submitReserveApproval(PROCUREMENT, 80, intent, signature);
        assertEq(escrow.humanNonces(vm.addr(HUMAN_KEY)), 0);
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT).state),
            uint8(IPoGRegistryV2.ProcurementState.PreAssessed)
        );
    }

    function test_CEO_GlobalAssetDeficitCannotBeShiftedToOtherProjectByRelease() public {
        CEOAdversarialTokenV2 adversarial = new CEOAdversarialTokenV2();
        _project(PROJECT, address(adversarial), foundation);
        bytes32 other = keccak256("CEO deficit project");
        _project(other, address(adversarial), foundation);
        _deposit(PROJECT, donorA, 100);
        _deposit(other, donorB, 100);
        _prepareReserved(PROCUREMENT, PROJECT, 100);
        _invoiceReceipt(PROCUREMENT, 100, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        adversarial.burnForTest(address(escrow), 50);
        vm.expectRevert();
        escrow.executeRelease(PROCUREMENT);
        assertEq(adversarial.balanceOf(address(escrow)), 150);
        assertEq(adversarial.balanceOf(foundation), 0);
        assertEq(escrow.getLedger(PROJECT).released, 0);
        assertEq(escrow.getLedger(PROJECT).reserved, 100);
        assertEq(escrow.totalAssetLiability(address(adversarial)), 200);
        assertEq(escrow.freeLocked(other), 100);
    }

    function test_CEO_GlobalAssetDeficitBlocksRefundAndNewCloseSnapshot() public {
        CEOAdversarialTokenV2 adversarial = new CEOAdversarialTokenV2();
        _project(PROJECT, address(adversarial), foundation);
        bytes32 other = keccak256("CEO deficit close project");
        _project(other, address(adversarial), foundation);
        _deposit(PROJECT, donorA, 100);
        _deposit(other, donorB, 100);
        _close(PROJECT);
        vm.prank(foundation);
        registry.requestClosing(other);
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            other,
            other,
            IProcurementEscrowV2.HumanAction.CloseProject,
            escrow.closeTermsHash(other),
            bytes32(0),
            HUMAN_KEY,
            _future()
        );
        escrow.submitCloseApproval(other, intent, _sign(HUMAN_KEY, escrow.intentDigest(intent)));
        adversarial.burnForTest(address(escrow), 50);
        vm.prank(donorA);
        vm.expectRevert();
        escrow.claimRefund(PROJECT);
        vm.expectRevert();
        escrow.executeClose(other);
        assertFalse(escrow.refundClaimed(PROJECT, donorA));
        assertEq(escrow.getLedger(PROJECT).refunded, 0);
        assertFalse(escrow.getLedger(other).refundSnapshotted);
        assertEq(escrow.totalAssetLiability(address(adversarial)), 200);
        assertEq(adversarial.balanceOf(address(escrow)), 150);
    }

    function test_CEO_ActiveReturnRejectedAtomicallyThenClosingReturnAllowed() public {
        _fundAndRelease(100, 80, 72);
        vm.prank(foundation);
        token.approve(address(escrow), 72);
        vm.prank(foundation);
        vm.expectRevert();
        escrow.returnReleasedFunds(PROCUREMENT, 1);
        assertEq(token.balanceOf(foundation), 72);
        assertEq(token.balanceOf(address(escrow)), 28);
        assertEq(token.allowance(foundation, address(escrow)), 72);
        assertEq(escrow.getLedger(PROJECT).returned, 0);
        assertEq(registry.getProcurement(PROCUREMENT).returnedAmount, 0);
        assertEq(escrow.totalAssetLiability(address(token)), 28);
        vm.startPrank(foundation);
        registry.requestClosing(PROJECT);
        escrow.returnReleasedFunds(PROCUREMENT, 1);
        vm.stopPrank();
        assertEq(escrow.freeLocked(PROJECT), 29);
        assertEq(token.balanceOf(foundation), 71);
        assertEq(escrow.getLedger(PROJECT).returned, 1);
    }

    function test_CEO_ClosingFreeLockedNeverDecreasesAndReturnsCannotFundNewPurchase() public {
        _fundAndRelease(300, 100, 100);
        bytes32 legitimate = keccak256("CEO existing legitimate invoice");
        bytes32 cancellable = keccak256("CEO existing cancellable reserve");
        bytes32 neverReserved = keccak256("CEO pending unreserved at Closing");
        _prepareReserved(legitimate, PROJECT, 120);
        _prepareReserved(cancellable, PROJECT, 30);
        _preparePre(neverReserved, PROJECT, 1);
        _reserveVote(neverReserved, 1, HUMAN_KEY, _future());
        vm.prank(foundation);
        registry.requestClosing(PROJECT);
        uint256 previous = escrow.freeLocked(PROJECT);
        assertEq(previous, 50);
        vm.startPrank(foundation);
        token.approve(address(escrow), 40);
        escrow.returnReleasedFunds(PROCUREMENT, 40);
        vm.stopPrank();
        previous = _assertFreeMonotonic(previous);
        assertEq(previous, 90);
        vm.prank(donorA);
        vm.expectRevert();
        escrow.deposit(PROJECT, 1);
        vm.prank(foundation);
        vm.expectRevert();
        registry.createProcurement(
            keccak256("spend returned funds"), PROJECT, makeAddr("vendor after Closing"), 1
        );
        vm.expectRevert();
        escrow.executeReserve(neverReserved, 1);
        vm.prank(foundation);
        registry.cancelUnreserved(neverReserved);
        previous = _assertFreeMonotonic(previous);
        _invoiceReceipt(legitimate, 90, _future());
        _assess(legitimate, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(legitimate, HUMAN_KEY, _future());
        escrow.executeRelease(legitimate);
        previous = _assertFreeMonotonic(previous);
        assertEq(previous, 120);
        _settle(legitimate);
        previous = _assertFreeMonotonic(previous);
        vm.prank(foundation);
        escrow.requestReservedCancellation(cancellable, keccak256("not executed"));
        _cancellationVote(cancellable, HUMAN_KEY, _future());
        escrow.executeReservedCancellation(cancellable);
        previous = _assertFreeMonotonic(previous);
        assertEq(previous, 150);
        assertEq(escrow.getLedger(PROJECT).returned, 40);
        assertEq(escrow.getLedger(PROJECT).reserved, 0);
        assertEq(escrow.getLedger(PROJECT).released, 190);
        assertEq(token.balanceOf(address(escrow)), 150);
        assertEq(escrow.totalAssetLiability(address(token)), 150);
        assertEq(registry.getProject(PROJECT).unresolvedProcurements, 1);
        vm.expectPartialRevert(ProcurementEscrowV2.UnresolvedProcurements.selector);
        escrow.executeClose(PROJECT);
        assertFalse(escrow.getLedger(PROJECT).refundSnapshotted);
    }

    function test_CEO_MaxUintDepositsHalfReleasedAndReturnedUseNetMath() public {
        uint256 maximum = type(uint256).max;
        uint256 half = maximum / 2;
        _fundAndRelease(maximum, maximum, half);
        assertEq(escrow.freeLocked(PROJECT), maximum - half);
        vm.startPrank(foundation);
        registry.requestClosing(PROJECT);
        token.approve(address(escrow), half);
        escrow.returnReleasedFunds(PROCUREMENT, half);
        vm.stopPrank();
        assertEq(escrow.freeLocked(PROJECT), maximum);
        assertEq(escrow.totalAssetLiability(address(token)), maximum);
        assertEq(token.balanceOf(address(escrow)), maximum);
        assertEq(escrow.getLedger(PROJECT).returned, half);
        assertEq(escrow.getLedger(PROJECT).released, half);
        assertNotEq(escrow.closeTermsHash(PROJECT), bytes32(0));
        vm.expectPartialRevert(ProcurementEscrowV2.UnresolvedProcurements.selector);
        escrow.executeClose(PROJECT);
    }

    function _assertFreeMonotonic(uint256 previous) private view returns (uint256 current) {
        current = escrow.freeLocked(PROJECT);
        assertGe(current, previous);
    }

    function assessForCEO(bytes32 id, IPoGRegistryV2.AssessmentStage stage, uint64 deadline)
        external
    {
        require(msg.sender == address(this), "test wrapper only");
        _assess(id, stage, deadline);
    }

    function reserveVoteForCEO(bytes32 id, uint256 amount, uint256 key, uint64 deadline) external {
        require(msg.sender == address(this), "test wrapper only");
        _reserveVote(id, amount, key, deadline);
    }

    function testFuzz_CEO_ProportionalRefundConservation(
        uint64 creditA,
        uint64 creditB,
        uint64 cost
    ) public {
        uint256 a = bound(uint256(creditA), 1, 1_000_000_000_000);
        uint256 b = bound(uint256(creditB), 1, 1_000_000_000_000);
        uint256 invoice = bound(uint256(cost), 1, a + b);
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, a);
        _deposit(PROJECT, donorB, b);
        _prepareReserved(PROCUREMENT, PROJECT, a + b);
        _invoiceReceipt(PROCUREMENT, invoice, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
        _settle(PROCUREMENT);
        _close(PROJECT);
        uint256 pool = a + b - invoice;
        if (pool != 0) {
            assertEq(escrow.refundEntitlement(PROJECT, donorA), Math.mulDiv(pool, a, a + b));
            assertEq(
                escrow.refundEntitlement(PROJECT, donorA)
                    + escrow.refundEntitlement(PROJECT, donorB),
                pool
            );
            vm.prank(donorB);
            escrow.claimRefund(PROJECT);
            vm.prank(donorA);
            escrow.claimRefund(PROJECT);
        }
        assertEq(escrow.getLedger(PROJECT).refunded, pool);
        assertEq(token.balanceOf(address(escrow)), 0);
        assertEq(escrow.totalAssetLiability(address(token)), 0);
        assertEq(token.balanceOf(foundation), invoice);
        assertEq(
            uint8(registry.getProject(PROJECT).state), uint8(IPoGRegistryV2.ProjectState.Closed)
        );
    }

    function _project(bytes32 id, address asset, address projectFoundation) private {
        address[] memory approvers = new address[](1);
        approvers[0] = vm.addr(HUMAN_KEY);
        vm.prank(projectFoundation);
        registry.createProject(id, recipient, asset, approvers, 1);
    }

    function _deposit(bytes32 id, address donor, uint256 amount) private {
        address asset = registry.getProject(id).asset;
        CEOMintableV2(asset).mint(donor, amount);
        vm.startPrank(donor);
        IERC20(asset).approve(address(escrow), amount);
        escrow.deposit(id, amount);
        vm.stopPrank();
    }

    function _preparePre(bytes32 id, bytes32 project, uint256 cap) private {
        vm.startPrank(registry.getProject(project).foundation);
        registry.createProcurement(id, project, makeAddr("CEO vendor"), cap);
        registry.recordPurchaseOrder(id, PO, keccak256("request"), keccak256("requested goods"));
        vm.stopPrank();
        _assess(id, IPoGRegistryV2.AssessmentStage.PrePurchase, _future());
    }

    function _prepareReserved(bytes32 id, bytes32 project, uint256 amount) private {
        _preparePre(id, project, amount);
        _reserveVote(id, amount, HUMAN_KEY, _future());
        escrow.executeReserve(id, amount);
    }

    function _invoiceReceipt(bytes32 id, uint256 amount, uint64 deadline) private {
        vm.prank(registry.getProject(registry.getProcurement(id).projectId).foundation);
        registry.recordInvoiceAndGoods(id, INVOICE_HASH, amount, GOODS);
        PoGRegistryV2.RecipientReceiptInput memory receipt = _receipt(id, deadline);
        registry.submitRecipientReceipt(receipt, _sign(RECIPIENT_KEY, _receiptDigest(receipt)));
    }

    function _receipt(bytes32 id, uint64 deadline)
        private
        view
        returns (PoGRegistryV2.RecipientReceiptInput memory r)
    {
        IPoGRegistryV2.ProcurementView memory p = registry.getProcurement(id);
        r = PoGRegistryV2.RecipientReceiptInput(
            p.projectId,
            id,
            recipient,
            p.vendor,
            p.poHash,
            p.invoiceHash,
            p.invoiceAmount,
            p.goodsHash,
            keccak256("GRN and photos"),
            registry.recipientNonces(recipient),
            deadline
        );
    }

    function _receiptDigest(PoGRegistryV2.RecipientReceiptInput memory r)
        private
        view
        returns (bytes32)
    {
        bytes32 typehash = keccak256(
            "RecipientReceipt(bytes32 projectId,bytes32 procurementId,address expectedRecipient,address vendor,bytes32 poHash,bytes32 invoiceHash,uint256 invoiceAmount,bytes32 goodsHash,bytes32 receiptEvidenceHash,uint256 nonce,uint64 deadline)"
        );
        bytes32 structHash = keccak256(
            abi.encode(
                typehash,
                r.projectId,
                r.procurementId,
                r.expectedRecipient,
                r.vendor,
                r.poHash,
                r.invoiceHash,
                r.invoiceAmount,
                r.goodsHash,
                r.receiptEvidenceHash,
                r.nonce,
                r.deadline
            )
        );
        return keccak256(
            abi.encodePacked(hex"1901", _domain("PoGRegistryV2", address(registry)), structHash)
        );
    }

    function _assess(bytes32 id, IPoGRegistryV2.AssessmentStage stage, uint64 deadline) private {
        IPoGRegistryV2.ProcurementView memory p = registry.getProcurement(id);
        uint256 nonce = registry.aiNonces(vm.addr(AI_KEY));
        PoGRegistryV2.AssessmentInput memory input = PoGRegistryV2.AssessmentInput(
            stage,
            id,
            bytes32(0),
            IPoGRegistryV2.AssessmentOutcome.Review,
            7_000,
            stage == IPoGRegistryV2.AssessmentStage.PrePurchase
                ? p.preEvidenceHash
                : p.finalEvidenceHash,
            keccak256(abi.encode("CEO AI report", nonce)),
            vm.addr(AI_KEY),
            nonce,
            deadline
        );
        input.assessmentId = registry.computeAssessmentId(input);
        registry.submitAIAssessment(input, _sign(AI_KEY, registry.assessmentDigest(input)));
    }

    function _intent(
        bytes32 target,
        bytes32 project,
        IProcurementEscrowV2.HumanAction action,
        bytes32 terms,
        bytes32 assessment,
        uint256 key,
        uint64 deadline
    ) private view returns (ProcurementEscrowV2.HumanIntent memory) {
        address signer = vm.addr(key);
        return ProcurementEscrowV2.HumanIntent(
            target,
            action,
            terms,
            assessment,
            signer,
            escrow.humanNonces(signer),
            deadline,
            registry.getProject(project).policyEpoch
        );
    }

    function _reserveVote(bytes32 id, uint256 amount, uint256 key, uint64 deadline) private {
        IPoGRegistryV2.ProcurementView memory p = registry.getProcurement(id);
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            id,
            p.projectId,
            IProcurementEscrowV2.HumanAction.Reserve,
            escrow.reserveTermsHash(id, amount),
            p.preAssessmentId,
            key,
            deadline
        );
        escrow.submitReserveApproval(id, amount, intent, _sign(key, escrow.intentDigest(intent)));
    }

    function _releaseVote(bytes32 id, uint256 key, uint64 deadline) private {
        IPoGRegistryV2.ProcurementView memory p = registry.getProcurement(id);
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            id,
            p.projectId,
            IProcurementEscrowV2.HumanAction.ReleaseToFoundation,
            escrow.releaseTermsHash(id),
            p.finalAssessmentId,
            key,
            deadline
        );
        escrow.submitReleaseApproval(id, intent, _sign(key, escrow.intentDigest(intent)));
    }

    function _settlementVote(bytes32 id, uint256 key, uint64 deadline) private {
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            id,
            registry.getProcurement(id).projectId,
            IProcurementEscrowV2.HumanAction.ConfirmMockPayment,
            escrow.settlementTermsHash(id),
            bytes32(0),
            key,
            deadline
        );
        escrow.submitSettlementApproval(id, intent, _sign(key, escrow.intentDigest(intent)));
    }

    function _cancellationVote(bytes32 id, uint256 key, uint64 deadline) private {
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            id,
            registry.getProcurement(id).projectId,
            IProcurementEscrowV2.HumanAction.CancelReserved,
            escrow.cancellationTermsHash(id),
            bytes32(0),
            key,
            deadline
        );
        escrow.submitCancellationApproval(id, intent, _sign(key, escrow.intentDigest(intent)));
    }

    function _close(bytes32 project) private {
        if (registry.getProject(project).state == IPoGRegistryV2.ProjectState.Active) {
            vm.prank(registry.getProject(project).foundation);
            registry.requestClosing(project);
        }
        ProcurementEscrowV2.HumanIntent memory intent = _intent(
            project,
            project,
            IProcurementEscrowV2.HumanAction.CloseProject,
            escrow.closeTermsHash(project),
            bytes32(0),
            HUMAN_KEY,
            _future()
        );
        escrow.submitCloseApproval(project, intent, _sign(HUMAN_KEY, escrow.intentDigest(intent)));
        escrow.executeClose(project);
    }

    function _recordSettlement(bytes32 id) private {
        vm.prank(registry.getProject(registry.getProcurement(id).projectId).foundation);
        registry.recordSettlement(
            id, keccak256("CEO mock conversion"), keccak256("CEO mock payment")
        );
    }

    function _settle(bytes32 id) private {
        _recordSettlement(id);
        _settlementVote(id, HUMAN_KEY, _future());
        escrow.executeSettlementConfirmation(id);
    }

    function _fundAndRelease(uint256 donated, uint256 reserved, uint256 invoice) private {
        _project(PROJECT, address(token), foundation);
        _deposit(PROJECT, donorA, donated);
        _prepareReserved(PROCUREMENT, PROJECT, reserved);
        _invoiceReceipt(PROCUREMENT, invoice, _future());
        _assess(PROCUREMENT, IPoGRegistryV2.AssessmentStage.FinalRelease, _future());
        _releaseVote(PROCUREMENT, HUMAN_KEY, _future());
        escrow.executeRelease(PROCUREMENT);
    }

    function _setThreeOfTwo(bytes32 project) private {
        address[] memory approvers = new address[](3);
        approvers[0] = vm.addr(HUMAN_KEY);
        approvers[1] = vm.addr(HUMAN_TWO_KEY);
        approvers[2] = vm.addr(HUMAN_THREE_KEY);
        vm.prank(registry.getProject(project).foundation);
        registry.updateApprovalPolicy(project, approvers, 2);
    }

    function _domain(string memory name, address verifyingContract) private view returns (bytes32) {
        return keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak256(bytes(name)),
                keccak256("2"),
                block.chainid,
                verifyingContract
            )
        );
    }

    function _sign(uint256 key, bytes32 digest) private pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }

    function _future() private view returns (uint64) {
        return uint64(vm.getBlockTimestamp() + 1 days);
    }
}
