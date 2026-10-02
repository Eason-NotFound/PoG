// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { Test } from "forge-std/Test.sol";
import { IERC1271 } from "@openzeppelin/contracts/interfaces/IERC1271.sol";

import { MockHKD } from "../src/MockHKD.sol";
import { PoGRegistryV2 } from "../src/PoGRegistryV2.sol";
import { ProcurementEscrowV2 } from "../src/ProcurementEscrowV2.sol";
import { IPoGRegistryV2 } from "../src/interfaces/IPoGRegistryV2.sol";
import { IProcurementEscrowV2 } from "../src/interfaces/IProcurementEscrowV2.sol";

contract ERC1271ApproverV2 is IERC1271 {
    mapping(bytes32 => bool) public approved;

    function setApproved(bytes32 digest, bool value) external {
        approved[digest] = value;
    }

    function isValidSignature(bytes32 hash, bytes calldata) external view returns (bytes4) {
        return approved[hash] ? IERC1271.isValidSignature.selector : bytes4(0xffffffff);
    }
}

contract ProcurementEscrowV2Test is Test {
    uint256 private constant AI_KEY = 0xA11CE;
    uint256 private constant RECIPIENT_KEY = 0xBEEF;
    bytes32 private constant PROJECT = keccak256("coder V2 project");
    bytes32 private constant PROCUREMENT = keccak256("coder V2 procurement");

    PoGRegistryV2 private registry;
    ProcurementEscrowV2 private escrow;
    MockHKD private token;
    ERC1271ApproverV2 private approver;
    address private foundation;
    address private recipient;
    address private donor;

    function setUp() public {
        vm.warp(1_900_000_000);
        foundation = makeAddr("coder foundation");
        recipient = vm.addr(RECIPIENT_KEY);
        donor = makeAddr("coder donor");
        registry = new PoGRegistryV2(address(this));
        escrow = new ProcurementEscrowV2(address(registry));
        token = new MockHKD();
        approver = new ERC1271ApproverV2();
        registry.bindEscrow(address(escrow));
        registry.setAISigner(vm.addr(AI_KEY), true);

        address[] memory approvers = new address[](1);
        approvers[0] = address(approver);
        vm.prank(foundation);
        registry.createProject(PROJECT, recipient, address(token), approvers, 1);
    }

    function test_RegistryHooksRejectCounterpartSpoofing() public {
        vm.expectPartialRevert(ProcurementEscrowV2.Unauthorized.selector);
        escrow.registerProjectFromRegistry(
            bytes32(uint256(2)), address(token), new address[](0), 0, 1
        );
        vm.expectPartialRevert(PoGRegistryV2.Unauthorized.selector);
        registry.markReservedFromEscrow(PROCUREMENT, 1);
    }

    function test_DepositCreditsOnlyCallerAndDirectTransferIsSurplus() public {
        token.mint(donor, 120);
        vm.startPrank(donor);
        token.approve(address(escrow), 100);
        escrow.deposit(PROJECT, 100);
        token.transfer(address(escrow), 20);
        vm.stopPrank();

        assertEq(escrow.donorCredit(PROJECT, donor), 100);
        assertEq(escrow.getLedger(PROJECT).deposits, 100);
        assertEq(escrow.totalAssetLiability(address(token)), 100);
        assertEq(token.balanceOf(address(escrow)), 120);
        assertEq(escrow.freeLocked(PROJECT), 100);
    }

    function test_ERC1271VoteIsStoredAndNotRevalidatedAtExecution() public {
        _deposit(100);
        _preparePreAssessment(100);

        // AI evidence alone cannot even enter the executable human-approval state.
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidState.selector);
        escrow.executeReserve(PROCUREMENT, 80);

        ProcurementEscrowV2.HumanIntent memory intent = _reserveIntent(80);
        bytes32 digest = escrow.intentDigest(intent);
        approver.setApproved(digest, true);
        escrow.submitReserveApproval(PROCUREMENT, 80, intent, hex"1234");
        approver.setApproved(digest, false);

        escrow.executeReserve(PROCUREMENT, 80);
        assertEq(escrow.getLedger(PROJECT).reserved, 80);
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT).state),
            uint8(IPoGRegistryV2.ProcurementState.Reserved)
        );
    }

    function test_HumanNonceReplayAndLiveRenewalAreRejected() public {
        _deposit(100);
        _preparePreAssessment(100);
        ProcurementEscrowV2.HumanIntent memory first = _reserveIntent(80);
        bytes32 firstDigest = escrow.intentDigest(first);
        approver.setApproved(firstDigest, true);
        escrow.submitReserveApproval(PROCUREMENT, 80, first, "");

        vm.expectPartialRevert(ProcurementEscrowV2.NonceMismatch.selector);
        escrow.submitReserveApproval(PROCUREMENT, 80, first, "");

        ProcurementEscrowV2.HumanIntent memory renewal = _reserveIntent(80);
        bytes32 renewalDigest = escrow.intentDigest(renewal);
        approver.setApproved(renewalDigest, true);
        vm.expectPartialRevert(ProcurementEscrowV2.DuplicateLiveVote.selector);
        escrow.submitReserveApproval(PROCUREMENT, 80, renewal, "");
        assertEq(escrow.humanNonces(address(approver)), 1);
    }

    function test_HumanCannotApproveWithoutCurrentAI() public {
        _deposit(100);
        vm.startPrank(foundation);
        registry.createProcurement(PROCUREMENT, PROJECT, makeAddr("vendor"), 100);
        registry.recordPurchaseOrder(
            PROCUREMENT, keccak256("PO"), keccak256("request"), keccak256("goods request")
        );
        vm.stopPrank();

        ProcurementEscrowV2.HumanIntent memory intent = _reserveIntent(80);
        approver.setApproved(escrow.intentDigest(intent), true);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidState.selector);
        escrow.submitReserveApproval(PROCUREMENT, 80, intent, "");
        assertEq(escrow.humanNonces(address(approver)), 0);
    }

    function test_FullFoundationReleaseSettlementAndRefundPath() public {
        _deposit(100);
        _preparePreAssessment(100);
        ProcurementEscrowV2.HumanIntent memory reserve = _reserveIntent(80);
        approver.setApproved(escrow.intentDigest(reserve), true);
        escrow.submitReserveApproval(PROCUREMENT, 80, reserve, "");
        escrow.executeReserve(PROCUREMENT, 80);

        vm.prank(foundation);
        registry.recordInvoiceAndGoods(
            PROCUREMENT, keccak256("invoice"), 72, keccak256("delivered goods")
        );
        PoGRegistryV2.RecipientReceiptInput memory receipt = _receipt();
        registry.submitRecipientReceipt(receipt, _sign(RECIPIENT_KEY, _receiptDigest(receipt)));
        _submitAssessment(IPoGRegistryV2.AssessmentStage.FinalRelease);

        bytes32 finalAssessmentId = registry.getProcurement(PROCUREMENT).finalAssessmentId;
        ProcurementEscrowV2.HumanIntent memory release = _intent(
            IProcurementEscrowV2.HumanAction.ReleaseToFoundation,
            escrow.releaseTermsHash(PROCUREMENT),
            finalAssessmentId
        );
        approver.setApproved(escrow.intentDigest(release), true);
        escrow.submitReleaseApproval(PROCUREMENT, release, "");
        escrow.executeRelease(PROCUREMENT);
        assertEq(token.balanceOf(foundation), 72);

        vm.prank(foundation);
        registry.recordSettlement(
            PROCUREMENT, keccak256("conversion evidence"), keccak256("vendor paid in full")
        );
        ProcurementEscrowV2.HumanIntent memory settlement = _intent(
            IProcurementEscrowV2.HumanAction.ConfirmMockPayment,
            escrow.settlementTermsHash(PROCUREMENT),
            bytes32(0)
        );
        approver.setApproved(escrow.intentDigest(settlement), true);
        escrow.submitSettlementApproval(PROCUREMENT, settlement, "");
        escrow.executeSettlementConfirmation(PROCUREMENT);

        vm.prank(foundation);
        registry.requestClosing(PROJECT);
        ProcurementEscrowV2.HumanIntent memory close = ProcurementEscrowV2.HumanIntent({
            targetId: PROJECT,
            action: IProcurementEscrowV2.HumanAction.CloseProject,
            termsHash: escrow.closeTermsHash(PROJECT),
            assessmentId: bytes32(0),
            signer: address(approver),
            nonce: escrow.humanNonces(address(approver)),
            deadline: uint64(block.timestamp + 1 days),
            policyEpoch: registry.getProject(PROJECT).policyEpoch
        });
        approver.setApproved(escrow.intentDigest(close), true);
        escrow.submitCloseApproval(PROJECT, close, "");
        escrow.executeClose(PROJECT);
        assertEq(escrow.refundEntitlement(PROJECT, donor), 28);
        vm.prank(donor);
        escrow.claimRefund(PROJECT);
        assertEq(token.balanceOf(donor), 28);
        assertEq(
            uint8(registry.getProject(PROJECT).state), uint8(IPoGRegistryV2.ProjectState.Closed)
        );
    }

    function test_PreAssessmentRevocationAfterVoteBlocksReserve() public {
        _deposit(100);
        _preparePreAssessment(100);
        _submitReserveVote(80);
        registry.setAISigner(vm.addr(AI_KEY), false);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidAssessment.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        assertEq(escrow.getLedger(PROJECT).reserved, 0);
    }

    function test_PreAssessmentExpiryAfterVoteBlocksReserve() public {
        _deposit(100);
        _preparePreAssessment(100);
        _submitReserveVote(80);
        vm.warp(block.timestamp + 1 days + 1);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidAssessment.selector);
        escrow.executeReserve(PROCUREMENT, 80);
        assertEq(escrow.getLedger(PROJECT).reserved, 0);
    }

    function test_FinalAssessmentRevocationAfterVoteBlocksRelease() public {
        _prepareReleasePending(uint64(block.timestamp + 1 days));
        registry.setAISigner(vm.addr(AI_KEY), false);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidAssessment.selector);
        escrow.executeRelease(PROCUREMENT);
        assertEq(escrow.getLedger(PROJECT).released, 0);
        assertEq(escrow.getLedger(PROJECT).reserved, 80);
    }

    function test_FinalAssessmentExpiryAfterVoteBlocksRelease() public {
        _prepareReleasePending(uint64(block.timestamp + 1));
        vm.warp(block.timestamp + 2);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidAssessment.selector);
        escrow.executeRelease(PROCUREMENT);
        assertEq(escrow.getLedger(PROJECT).released, 0);
        assertEq(escrow.getLedger(PROJECT).reserved, 80);
    }

    function test_AIAssessmentRejectsWrongEvidenceDomainAndReplayedNonce() public {
        _deposit(100);
        _preparePurchaseOrder(100);
        IPoGRegistryV2.ProcurementView memory procurement = registry.getProcurement(PROCUREMENT);
        PoGRegistryV2.AssessmentInput memory input = _assessmentInput(
            IPoGRegistryV2.AssessmentStage.PrePurchase,
            procurement.preEvidenceHash,
            0,
            keccak256("first report"),
            uint64(block.timestamp + 1 days)
        );

        input.evidenceHash = keccak256("wrong evidence");
        input.assessmentId = registry.computeAssessmentId(input);
        bytes memory wrongEvidenceSignature = _sign(AI_KEY, registry.assessmentDigest(input));
        vm.expectPartialRevert(PoGRegistryV2.InvalidEvidence.selector);
        registry.submitAIAssessment(input, wrongEvidenceSignature);

        input.evidenceHash = procurement.preEvidenceHash;
        input.assessmentId = registry.computeAssessmentId(input);
        bytes32 aiV1Digest =
            _assessmentDigestWithDomain(input, keccak256("PoGRegistryV2"), keccak256("1"));
        bytes memory wrongDomainSignature = _sign(AI_KEY, aiV1Digest);
        vm.expectPartialRevert(PoGRegistryV2.InvalidSignature.selector);
        registry.submitAIAssessment(input, wrongDomainSignature);
        assertEq(registry.aiNonces(vm.addr(AI_KEY)), 0);

        registry.submitAIAssessment(input, _sign(AI_KEY, registry.assessmentDigest(input)));
        input.reportHash = keccak256("new report with stale nonce");
        input.assessmentId = registry.computeAssessmentId(input);
        bytes memory staleNonceSignature = _sign(AI_KEY, registry.assessmentDigest(input));
        vm.expectPartialRevert(PoGRegistryV2.NonceMismatch.selector);
        registry.submitAIAssessment(input, staleNonceSignature);
        assertEq(registry.aiNonces(vm.addr(AI_KEY)), 1);
    }

    function test_HumanIntentTamperingAndWrongDomainDoNotConsumeNonce() public {
        _deposit(100);
        _preparePreAssessment(100);
        ProcurementEscrowV2.HumanIntent memory intent = _reserveIntent(80);

        intent.action = IProcurementEscrowV2.HumanAction.ReleaseToFoundation;
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidAction.selector);
        escrow.submitReserveApproval(PROCUREMENT, 80, intent, "");
        intent.action = IProcurementEscrowV2.HumanAction.Reserve;
        intent.termsHash = keccak256("tampered terms");
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidTerms.selector);
        escrow.submitReserveApproval(PROCUREMENT, 80, intent, "");

        intent.termsHash = escrow.reserveTermsHash(PROCUREMENT, 80);
        bytes32 humanV1Digest =
            _humanDigestWithDomain(intent, keccak256("ProcurementEscrowV2"), keccak256("1"));
        approver.setApproved(humanV1Digest, true);
        vm.expectPartialRevert(ProcurementEscrowV2.InvalidSignature.selector);
        escrow.submitReserveApproval(PROCUREMENT, 80, intent, "");
        assertEq(escrow.humanNonces(address(approver)), 0);
    }

    function test_RecipientExpiryAndWrongDomainDoNotConsumeNonce() public {
        _deposit(100);
        _preparePreAssessment(100);
        _submitReserveVote(80);
        escrow.executeReserve(PROCUREMENT, 80);
        vm.prank(foundation);
        registry.recordInvoiceAndGoods(
            PROCUREMENT, keccak256("invoice"), 72, keccak256("delivered goods")
        );

        PoGRegistryV2.RecipientReceiptInput memory receipt = _receipt();
        receipt.deadline = uint64(block.timestamp - 1);
        bytes memory expiredSignature = _sign(RECIPIENT_KEY, _receiptDigest(receipt));
        vm.expectPartialRevert(PoGRegistryV2.SignatureExpired.selector);
        registry.submitRecipientReceipt(receipt, expiredSignature);

        receipt.deadline = uint64(block.timestamp + 1 days);
        bytes32 wrongDomainDigest =
            _receiptDigestWithDomain(receipt, keccak256("PoGRegistry"), keccak256("1"));
        bytes memory receiptWrongDomainSignature = _sign(RECIPIENT_KEY, wrongDomainDigest);
        vm.expectPartialRevert(PoGRegistryV2.InvalidSignature.selector);
        registry.submitRecipientReceipt(receipt, receiptWrongDomainSignature);
        assertEq(registry.recipientNonces(recipient), 0);
    }

    function testFuzz_DepositMaintainsLedgerAndGlobalLiability(uint96 rawAmount) public {
        uint256 amount = bound(uint256(rawAmount), 1, type(uint96).max);
        _deposit(amount);
        assertEq(escrow.donorCredit(PROJECT, donor), amount);
        assertEq(escrow.getLedger(PROJECT).deposits, amount);
        assertEq(escrow.freeLocked(PROJECT), amount);
        assertEq(escrow.totalAssetLiability(address(token)), amount);
        assertEq(token.balanceOf(address(escrow)), amount);
    }

    function _deposit(uint256 amount) private {
        token.mint(donor, amount);
        vm.startPrank(donor);
        token.approve(address(escrow), amount);
        escrow.deposit(PROJECT, amount);
        vm.stopPrank();
    }

    function _preparePreAssessment(uint256 cap) private {
        _preparePurchaseOrder(cap);
        _submitAssessment(IPoGRegistryV2.AssessmentStage.PrePurchase);
    }

    function _preparePurchaseOrder(uint256 cap) private {
        vm.startPrank(foundation);
        registry.createProcurement(PROCUREMENT, PROJECT, makeAddr("vendor"), cap);
        registry.recordPurchaseOrder(
            PROCUREMENT, keccak256("PO"), keccak256("request"), keccak256("goods request")
        );
        vm.stopPrank();
    }

    function _submitAssessment(IPoGRegistryV2.AssessmentStage stage) private {
        IPoGRegistryV2.ProcurementView memory procurement = registry.getProcurement(PROCUREMENT);
        bytes32 evidenceHash = stage == IPoGRegistryV2.AssessmentStage.PrePurchase
            ? procurement.preEvidenceHash
            : procurement.finalEvidenceHash;
        PoGRegistryV2.AssessmentInput memory input = _assessmentInput(
            stage,
            evidenceHash,
            registry.aiNonces(vm.addr(AI_KEY)),
            keccak256(abi.encode("AI report", stage)),
            uint64(block.timestamp + 1 days)
        );
        input.assessmentId = registry.computeAssessmentId(input);
        registry.submitAIAssessment(input, _sign(AI_KEY, registry.assessmentDigest(input)));
    }

    function _submitAssessmentWithDeadline(IPoGRegistryV2.AssessmentStage stage, uint64 deadline)
        private
    {
        IPoGRegistryV2.ProcurementView memory procurement = registry.getProcurement(PROCUREMENT);
        bytes32 evidenceHash = stage == IPoGRegistryV2.AssessmentStage.PrePurchase
            ? procurement.preEvidenceHash
            : procurement.finalEvidenceHash;
        PoGRegistryV2.AssessmentInput memory input = _assessmentInput(
            stage,
            evidenceHash,
            registry.aiNonces(vm.addr(AI_KEY)),
            keccak256(abi.encode("AI report", stage, deadline)),
            deadline
        );
        input.assessmentId = registry.computeAssessmentId(input);
        registry.submitAIAssessment(input, _sign(AI_KEY, registry.assessmentDigest(input)));
    }

    function _assessmentInput(
        IPoGRegistryV2.AssessmentStage stage,
        bytes32 evidenceHash,
        uint256 nonce,
        bytes32 reportHash,
        uint64 deadline
    ) private view returns (PoGRegistryV2.AssessmentInput memory) {
        PoGRegistryV2.AssessmentInput memory input = PoGRegistryV2.AssessmentInput({
            stage: stage,
            procurementId: PROCUREMENT,
            assessmentId: bytes32(0),
            outcome: IPoGRegistryV2.AssessmentOutcome.Review,
            riskScoreBps: 5_000,
            evidenceHash: evidenceHash,
            reportHash: reportHash,
            signer: vm.addr(AI_KEY),
            nonce: nonce,
            deadline: deadline
        });
        input.assessmentId = registry.computeAssessmentId(input);
        return input;
    }

    function _submitReserveVote(uint256 amount) private {
        ProcurementEscrowV2.HumanIntent memory intent = _reserveIntent(amount);
        approver.setApproved(escrow.intentDigest(intent), true);
        escrow.submitReserveApproval(PROCUREMENT, amount, intent, "");
    }

    function _prepareReleasePending(uint64 finalAssessmentDeadline) private {
        _deposit(100);
        _preparePreAssessment(100);
        _submitReserveVote(80);
        escrow.executeReserve(PROCUREMENT, 80);
        vm.prank(foundation);
        registry.recordInvoiceAndGoods(
            PROCUREMENT, keccak256("invoice"), 72, keccak256("delivered goods")
        );
        PoGRegistryV2.RecipientReceiptInput memory receipt = _receipt();
        registry.submitRecipientReceipt(receipt, _sign(RECIPIENT_KEY, _receiptDigest(receipt)));
        _submitAssessmentWithDeadline(
            IPoGRegistryV2.AssessmentStage.FinalRelease, finalAssessmentDeadline
        );
        ProcurementEscrowV2.HumanIntent memory release = _intent(
            IProcurementEscrowV2.HumanAction.ReleaseToFoundation,
            escrow.releaseTermsHash(PROCUREMENT),
            registry.getProcurement(PROCUREMENT).finalAssessmentId
        );
        approver.setApproved(escrow.intentDigest(release), true);
        escrow.submitReleaseApproval(PROCUREMENT, release, "");
    }

    function _reserveIntent(uint256 amount)
        private
        view
        returns (ProcurementEscrowV2.HumanIntent memory)
    {
        return ProcurementEscrowV2.HumanIntent({
            targetId: PROCUREMENT,
            action: IProcurementEscrowV2.HumanAction.Reserve,
            termsHash: escrow.reserveTermsHash(PROCUREMENT, amount),
            assessmentId: registry.getProcurement(PROCUREMENT).preAssessmentId,
            signer: address(approver),
            nonce: escrow.humanNonces(address(approver)),
            deadline: uint64(block.timestamp + 1 days),
            policyEpoch: registry.getProject(PROJECT).policyEpoch
        });
    }

    function _intent(
        IProcurementEscrowV2.HumanAction action,
        bytes32 termsHash,
        bytes32 assessmentId
    ) private view returns (ProcurementEscrowV2.HumanIntent memory) {
        return ProcurementEscrowV2.HumanIntent({
            targetId: PROCUREMENT,
            action: action,
            termsHash: termsHash,
            assessmentId: assessmentId,
            signer: address(approver),
            nonce: escrow.humanNonces(address(approver)),
            deadline: uint64(block.timestamp + 1 days),
            policyEpoch: registry.getProject(PROJECT).policyEpoch
        });
    }

    function _receipt() private view returns (PoGRegistryV2.RecipientReceiptInput memory receipt) {
        IPoGRegistryV2.ProcurementView memory procurement = registry.getProcurement(PROCUREMENT);
        receipt = PoGRegistryV2.RecipientReceiptInput({
            projectId: PROJECT,
            procurementId: PROCUREMENT,
            expectedRecipient: recipient,
            vendor: procurement.vendor,
            poHash: procurement.poHash,
            invoiceHash: procurement.invoiceHash,
            invoiceAmount: procurement.invoiceAmount,
            goodsHash: procurement.goodsHash,
            receiptEvidenceHash: keccak256("recipient receipt evidence"),
            nonce: registry.recipientNonces(recipient),
            deadline: uint64(block.timestamp + 1 days)
        });
    }

    function _receiptDigest(PoGRegistryV2.RecipientReceiptInput memory receipt)
        private
        view
        returns (bytes32)
    {
        return _receiptDigestWithDomain(receipt, keccak256("PoGRegistryV2"), keccak256("2"));
    }

    function _assessmentDigestWithDomain(
        PoGRegistryV2.AssessmentInput memory input,
        bytes32 nameHash,
        bytes32 versionHash
    ) private view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                registry.AI_ASSESSMENT_TYPEHASH(),
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
        return keccak256(
            abi.encodePacked(
                hex"1901", _domainSeparator(nameHash, versionHash, address(registry)), structHash
            )
        );
    }

    function _humanDigestWithDomain(
        ProcurementEscrowV2.HumanIntent memory intent,
        bytes32 nameHash,
        bytes32 versionHash
    ) private view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                escrow.HUMAN_INTENT_TYPEHASH(),
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
        return keccak256(
            abi.encodePacked(
                hex"1901", _domainSeparator(nameHash, versionHash, address(escrow)), structHash
            )
        );
    }

    function _receiptDigestWithDomain(
        PoGRegistryV2.RecipientReceiptInput memory receipt,
        bytes32 nameHash,
        bytes32 versionHash
    ) private view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                registry.RECIPIENT_RECEIPT_TYPEHASH(),
                receipt.projectId,
                receipt.procurementId,
                receipt.expectedRecipient,
                receipt.vendor,
                receipt.poHash,
                receipt.invoiceHash,
                receipt.invoiceAmount,
                receipt.goodsHash,
                receipt.receiptEvidenceHash,
                receipt.nonce,
                receipt.deadline
            )
        );
        bytes32 domainSeparator = _domainSeparator(nameHash, versionHash, address(registry));
        return keccak256(abi.encodePacked(hex"1901", domainSeparator, structHash));
    }

    function _domainSeparator(bytes32 nameHash, bytes32 versionHash, address verifier)
        private
        view
        returns (bytes32)
    {
        return keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                nameHash,
                versionHash,
                block.chainid,
                verifier
            )
        );
    }

    function _sign(uint256 privateKey, bytes32 digest) private pure returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        return abi.encodePacked(r, s, v);
    }
}
