// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import { Test } from "forge-std/Test.sol";
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";
import { Pausable } from "@openzeppelin/contracts/utils/Pausable.sol";

import { PoGRegistry } from "../src/PoGRegistry.sol";
import { MockAsset, NoDecimalsAsset } from "./mocks/MockAsset.sol";
import { MockProcurementEscrow } from "./mocks/MockProcurementEscrow.sol";

contract PoGRegistryTest is Test {
    uint256 internal constant AI_KEY = 0xA11CE;
    uint256 internal constant OTHER_KEY = 0xB0B;
    uint256 internal constant CAP = 100_000_000;
    uint256 internal constant RESERVE = 80_000_000;
    uint256 internal constant INVOICE = 72_000_000;

    bytes32 internal constant PROJECT_ID = keccak256("project");
    bytes32 internal constant PROCUREMENT_ID = keccak256("procurement");
    bytes32 internal constant REQUEST_HASH = keccak256("request");
    bytes32 internal constant QUOTE_HASH = keccak256("quotes");
    bytes32 internal constant PO_HASH = keccak256("po");
    bytes32 internal constant GRN_HASH = keccak256("grn");
    bytes32 internal constant INVOICE_HASH = keccak256("invoice");

    address internal owner = makeAddr("owner");
    address internal foundation = makeAddr("foundation");
    address internal recipient = makeAddr("recipient");
    address internal vendor = makeAddr("vendor");
    address internal approver = makeAddr("approver");
    address internal approverTwo = makeAddr("approverTwo");
    address internal attacker = makeAddr("attacker");
    address internal aiSigner;

    PoGRegistry internal registry;
    MockProcurementEscrow internal escrow;
    MockAsset internal asset;

    function setUp() public {
        vm.warp(1_800_000_000);
        aiSigner = vm.addr(AI_KEY);
        registry = new PoGRegistry(owner);
        asset = new MockAsset(6);
        escrow = new MockProcurementEscrow(address(registry));

        vm.prank(owner);
        registry.bindEscrow(address(escrow));
        vm.prank(owner);
        registry.setAISigner(aiSigner, true);
    }

    // ---------------------------------------------------------------------
    // Binding and administration
    // ---------------------------------------------------------------------

    function test_BindOwnerOnly() public {
        PoGRegistry fresh = new PoGRegistry(owner);
        MockProcurementEscrow freshEscrow = new MockProcurementEscrow(address(fresh));

        vm.prank(attacker);
        vm.expectRevert(
            abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker)
        );
        fresh.bindEscrow(address(freshEscrow));
    }

    function test_BindRejectsZeroAndEOA() public {
        PoGRegistry fresh = new PoGRegistry(owner);

        vm.startPrank(owner);
        vm.expectPartialRevert(PoGRegistry.InvalidEscrow.selector);
        fresh.bindEscrow(address(0));
        vm.expectPartialRevert(PoGRegistry.InvalidEscrow.selector);
        fresh.bindEscrow(attacker);
        vm.stopPrank();
    }

    function test_BindRejectsWrongBackReference() public {
        PoGRegistry fresh = new PoGRegistry(owner);
        MockProcurementEscrow wrong = new MockProcurementEscrow(attacker);

        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(
                PoGRegistry.InvalidEscrowRegistry.selector, address(fresh), attacker
            )
        );
        fresh.bindEscrow(address(wrong));
    }

    function test_BindExactlyOnce() public {
        vm.prank(owner);
        vm.expectPartialRevert(PoGRegistry.EscrowAlreadyBound.selector);
        registry.bindEscrow(address(escrow));
    }

    function test_ProjectCreationRequiresBinding() public {
        PoGRegistry fresh = new PoGRegistry(owner);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.EscrowNotBound.selector);
        fresh.createProject(PROJECT_ID, recipient, address(asset), _oneApprover(), 1);
    }

    function test_AdminCanPauseButCannotUseFoundationAuthority() public {
        _createProject();
        vm.prank(owner);
        registry.pause();

        vm.prank(foundation);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);

        vm.prank(owner);
        registry.unpause();

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(PoGRegistry.UnauthorizedCaller.selector, owner));
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);
    }

    function test_AISignerManagementIsOwnerOnlyAndRevocable() public {
        vm.prank(attacker);
        vm.expectRevert(
            abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, attacker)
        );
        registry.setAISigner(vm.addr(OTHER_KEY), true);

        vm.prank(owner);
        registry.setAISigner(aiSigner, false);
        assertFalse(registry.aiSigners(aiSigner));
    }

    // ---------------------------------------------------------------------
    // Project and procurement creation
    // ---------------------------------------------------------------------

    function test_CreateProjectStoresFieldsAndRegistersAtomically() public {
        _createProject();
        PoGRegistry.RegistryProject memory project = registry.getProject(PROJECT_ID);

        assertEq(project.projectId, PROJECT_ID);
        assertEq(project.foundation, foundation);
        assertEq(project.recipient, recipient);
        assertEq(project.asset, address(asset));
        assertEq(project.assetDecimals, 6);
        assertEq(project.approverEpoch, 1);
        assertEq(project.approvalThreshold, 1);
        assertTrue(escrow.registered(PROJECT_ID));
        assertEq(escrow.epoch(PROJECT_ID), 1);
        assertEq(escrow.threshold(PROJECT_ID), 1);
    }

    function test_CreateProjectRejectsDuplicatesAndZeroValues() public {
        _createProject();

        vm.startPrank(foundation);
        vm.expectPartialRevert(PoGRegistry.DuplicateProject.selector);
        registry.createProject(PROJECT_ID, recipient, address(asset), _oneApprover(), 1);
        vm.expectPartialRevert(PoGRegistry.ZeroId.selector);
        registry.createProject(bytes32(0), recipient, address(asset), _oneApprover(), 1);
        vm.expectPartialRevert(PoGRegistry.ZeroAddress.selector);
        registry.createProject(
            keccak256("zero-recipient"), address(0), address(asset), _oneApprover(), 1
        );
        vm.stopPrank();
    }

    function test_CreateProjectValidatesPolicy() public {
        address[] memory none = new address[](0);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.InvalidApprovalPolicy.selector);
        registry.createProject(PROJECT_ID, recipient, address(asset), none, 0);

        address[] memory duplicate = new address[](2);
        duplicate[0] = approver;
        duplicate[1] = approver;
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.DuplicateApprover.selector);
        registry.createProject(PROJECT_ID, recipient, address(asset), duplicate, 1);

        address[] memory zeroApprover = new address[](1);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.ZeroAddress.selector);
        registry.createProject(PROJECT_ID, recipient, address(asset), zeroApprover, 1);
    }

    function test_CreateProjectRequiresSixDecimalContractAsset() public {
        MockAsset wrongDecimals = new MockAsset(18);
        vm.prank(foundation);
        vm.expectRevert(
            abi.encodeWithSelector(PoGRegistry.UnsupportedAssetDecimals.selector, uint8(18))
        );
        registry.createProject(PROJECT_ID, recipient, address(wrongDecimals), _oneApprover(), 1);

        NoDecimalsAsset noDecimals = new NoDecimalsAsset();
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.UnsupportedAsset.selector);
        registry.createProject(PROJECT_ID, recipient, address(noDecimals), _oneApprover(), 1);

        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.UnsupportedAsset.selector);
        registry.createProject(PROJECT_ID, recipient, attacker, _oneApprover(), 1);
    }

    function test_RevertingRegisterHookRollsBackProject() public {
        PoGRegistry fresh = new PoGRegistry(owner);
        MockProcurementEscrow badEscrow = new MockProcurementEscrow(address(fresh));
        badEscrow.setReverts(true, false, false);
        vm.prank(owner);
        fresh.bindEscrow(address(badEscrow));

        vm.prank(foundation);
        vm.expectRevert(MockProcurementEscrow.HookReverted.selector);
        fresh.createProject(PROJECT_ID, recipient, address(asset), _oneApprover(), 1);

        assertFalse(fresh.projectExists(PROJECT_ID));
        assertEq(fresh.projectCount(), 0);
    }

    function test_MaliciousRegisterHookCannotReenter() public {
        PoGRegistry fresh = new PoGRegistry(owner);
        MockProcurementEscrow malicious = new MockProcurementEscrow(address(fresh));
        malicious.setAttemptReentry(true);
        vm.prank(owner);
        fresh.bindEscrow(address(malicious));

        vm.prank(foundation);
        fresh.createProject(PROJECT_ID, recipient, address(asset), _oneApprover(), 1);

        assertFalse(malicious.reentrySucceeded());
        assertEq(fresh.projectCount(), 1);
    }

    function test_CreateProcurementAuthAndValidation() public {
        _createProject();

        vm.prank(attacker);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);

        vm.startPrank(foundation);
        vm.expectPartialRevert(PoGRegistry.ZeroId.selector);
        registry.createProcurement(bytes32(0), PROJECT_ID, vendor, CAP);
        vm.expectPartialRevert(PoGRegistry.ZeroAddress.selector);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, address(0), CAP);
        vm.expectPartialRevert(PoGRegistry.ZeroAmount.selector);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, 0);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);
        vm.expectPartialRevert(PoGRegistry.DuplicateProcurement.selector);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------------
    // Evidence, AI signatures, and nonces
    // ---------------------------------------------------------------------

    function testFuzz_ExactPreEvidenceHash(bytes32 requestHash, bytes32 quoteHash) public {
        vm.assume(requestHash != bytes32(0) && quoteHash != bytes32(0));
        _createProjectAndProcurement();

        bytes32 expected = keccak256(
            abi.encode(
                "POG_PRE_EVIDENCE_V1",
                PROJECT_ID,
                PROCUREMENT_ID,
                vendor,
                address(asset),
                CAP,
                requestHash,
                quoteHash
            )
        );
        assertEq(registry.computePreEvidenceHash(PROCUREMENT_ID, requestHash, quoteHash), expected);

        vm.prank(foundation);
        registry.recordPrePurchaseEvidence(PROCUREMENT_ID, requestHash, quoteHash);
        assertEq(registry.getProcurement(PROCUREMENT_ID).preEvidenceHash, expected);
    }

    function test_PreEvidenceRejectsUnauthorizedZeroAndDuplicate() public {
        _createProjectAndProcurement();
        vm.prank(attacker);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.recordPrePurchaseEvidence(PROCUREMENT_ID, REQUEST_HASH, QUOTE_HASH);

        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.ZeroHash.selector);
        registry.recordPrePurchaseEvidence(PROCUREMENT_ID, bytes32(0), QUOTE_HASH);

        _recordPreEvidence();
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.recordPrePurchaseEvidence(PROCUREMENT_ID, REQUEST_HASH, QUOTE_HASH);
    }

    function test_ValidAIAssessmentAcceptedAndReplayRejected() public {
        _preparePreEvidence();
        (PoGRegistry.AIAssessment memory assessment, bytes memory signature) = _assessment(
            PROCUREMENT_ID,
            PoGRegistry.AssessmentStage.PreProcurement,
            registry.getProcurement(PROCUREMENT_ID).preEvidenceHash,
            AI_KEY,
            0
        );

        registry.submitAIAssessment(assessment, signature);
        assertEq(registry.aiNonces(aiSigner), 1);
        assertTrue(registry.assessmentExists(assessment.assessmentId));
        assertTrue(registry.isAssessmentCurrent(assessment.assessmentId));
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT_ID).status),
            uint8(PoGRegistry.ProcurementStatus.PreAssessed)
        );

        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.submitAIAssessment(assessment, signature);
    }

    function test_ExactAssessmentIdFormula() public {
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;
        (PoGRegistry.AIAssessment memory assessment,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );

        bytes32 expected = keccak256(
            abi.encode(
                assessment.procurementId,
                assessment.stage,
                assessment.reportHash,
                assessment.evidenceHash,
                assessment.modelId,
                assessment.outcome,
                assessment.riskScoreBps,
                assessment.issuedAt,
                assessment.expiresAt,
                assessment.nonce,
                assessment.aiSigner
            )
        );
        assertEq(assessment.assessmentId, expected);
        assertEq(registry.computeAssessmentId(assessment), expected);
    }

    function test_AIAssessmentRejectsUnauthorizedAndWrongSignature() public {
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;

        (PoGRegistry.AIAssessment memory unauthorized, bytes memory unauthorizedSignature) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, OTHER_KEY, 0
        );
        vm.expectPartialRevert(PoGRegistry.UnauthorizedAISigner.selector);
        registry.submitAIAssessment(unauthorized, unauthorizedSignature);

        (PoGRegistry.AIAssessment memory assessment,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        (, bytes memory wrongSignature) = _sign(assessment, OTHER_KEY, registry);
        vm.expectPartialRevert(PoGRegistry.InvalidSignature.selector);
        registry.submitAIAssessment(assessment, wrongSignature);
    }

    function test_AIAssessmentRejectsWrongDomain() public {
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;
        (PoGRegistry.AIAssessment memory assessment,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        PoGRegistry otherDomain = new PoGRegistry(owner);
        (, bytes memory signature) = _sign(assessment, AI_KEY, otherDomain);

        vm.expectPartialRevert(PoGRegistry.InvalidSignature.selector);
        registry.submitAIAssessment(assessment, signature);
    }

    function test_AIAssessmentRejectsWrongStageEvidenceAndId() public {
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;

        (PoGRegistry.AIAssessment memory wrongStage, bytes memory wrongStageSignature) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.FinalPayment, evidence, AI_KEY, 0
        );
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.submitAIAssessment(wrongStage, wrongStageSignature);

        (PoGRegistry.AIAssessment memory wrongEvidence, bytes memory wrongEvidenceSignature) = _assessment(
            PROCUREMENT_ID,
            PoGRegistry.AssessmentStage.PreProcurement,
            keccak256("wrong"),
            AI_KEY,
            0
        );
        vm.expectPartialRevert(PoGRegistry.InvalidEvidenceHash.selector);
        registry.submitAIAssessment(wrongEvidence, wrongEvidenceSignature);

        (PoGRegistry.AIAssessment memory wrongId,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        wrongId.assessmentId = keccak256("wrong-id");
        (, bytes memory wrongIdSignature) = _sign(wrongId, AI_KEY, registry);
        vm.expectPartialRevert(PoGRegistry.InvalidAssessmentId.selector);
        registry.submitAIAssessment(wrongId, wrongIdSignature);
    }

    function test_AIAssessmentStrictRiskAndTimeWindows() public {
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;

        (PoGRegistry.AIAssessment memory risk,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        risk.riskScoreBps = 10_001;
        risk.assessmentId = registry.computeAssessmentId(risk);
        (, bytes memory riskSignature) = _sign(risk, AI_KEY, registry);
        vm.expectPartialRevert(PoGRegistry.InvalidRiskScore.selector);
        registry.submitAIAssessment(risk, riskSignature);

        (PoGRegistry.AIAssessment memory future,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        future.issuedAt = uint64(block.timestamp + 1);
        future.expiresAt = uint64(block.timestamp + 2);
        future.assessmentId = registry.computeAssessmentId(future);
        (, bytes memory futureSignature) = _sign(future, AI_KEY, registry);
        vm.expectPartialRevert(PoGRegistry.InvalidAssessmentWindow.selector);
        registry.submitAIAssessment(future, futureSignature);

        (PoGRegistry.AIAssessment memory inverted,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        inverted.expiresAt = inverted.issuedAt;
        inverted.assessmentId = registry.computeAssessmentId(inverted);
        (, bytes memory invertedSignature) = _sign(inverted, AI_KEY, registry);
        vm.expectPartialRevert(PoGRegistry.InvalidAssessmentWindow.selector);
        registry.submitAIAssessment(inverted, invertedSignature);

        (PoGRegistry.AIAssessment memory expired,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        expired.issuedAt = uint64(block.timestamp - 2);
        expired.expiresAt = uint64(block.timestamp - 1);
        expired.assessmentId = registry.computeAssessmentId(expired);
        (, bytes memory expiredSignature) = _sign(expired, AI_KEY, registry);
        vm.expectPartialRevert(PoGRegistry.AssessmentExpired.selector);
        registry.submitAIAssessment(expired, expiredSignature);
    }

    function test_ExactExpiryBoundaryAcceptedThenBecomesStale() public {
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;
        (PoGRegistry.AIAssessment memory assessment,) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0
        );
        assessment.issuedAt = uint64(block.timestamp - 1);
        assessment.expiresAt = uint64(block.timestamp);
        assessment.assessmentId = registry.computeAssessmentId(assessment);
        (, bytes memory signature) = _sign(assessment, AI_KEY, registry);
        registry.submitAIAssessment(assessment, signature);

        assertTrue(registry.isAssessmentCurrent(assessment.assessmentId));
        vm.warp(block.timestamp + 1);
        assertFalse(registry.isAssessmentCurrent(assessment.assessmentId));
        vm.expectPartialRevert(PoGRegistry.AssessmentNotCurrent.selector);
        escrow.markReservePending(PROCUREMENT_ID);
    }

    function testFuzz_ExactNextNonceRejectsAnyOtherNonce(uint64 badNonce) public {
        vm.assume(badNonce != 0);
        _preparePreEvidence();
        bytes32 evidence = registry.getProcurement(PROCUREMENT_ID).preEvidenceHash;
        (PoGRegistry.AIAssessment memory assessment, bytes memory signature) = _assessment(
            PROCUREMENT_ID, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, badNonce
        );

        vm.expectPartialRevert(PoGRegistry.NonceMismatch.selector);
        registry.submitAIAssessment(assessment, signature);
    }

    function test_NonceAdvancesAcrossProcurements() public {
        _preparePreAssessed();
        bytes32 secondId = keccak256("second");
        vm.prank(foundation);
        registry.createProcurement(secondId, PROJECT_ID, vendor, CAP);
        vm.prank(foundation);
        registry.recordPrePurchaseEvidence(secondId, REQUEST_HASH, QUOTE_HASH);
        bytes32 evidence = registry.getProcurement(secondId).preEvidenceHash;

        (PoGRegistry.AIAssessment memory stale, bytes memory signature) =
            _assessment(secondId, PoGRegistry.AssessmentStage.PreProcurement, evidence, AI_KEY, 0);
        vm.expectPartialRevert(PoGRegistry.NonceMismatch.selector);
        registry.submitAIAssessment(stale, signature);
    }

    // ---------------------------------------------------------------------
    // Lifecycle and counterpart callbacks
    // ---------------------------------------------------------------------

    function test_AllLegalTransitionsAndExactFinalEvidence() public {
        _preparePreAssessed();
        escrow.markReservePending(PROCUREMENT_ID);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);

        vm.startPrank(foundation);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);
        registry.recordDelivery(PROCUREMENT_ID, GRN_HASH);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, INVOICE);
        vm.stopPrank();

        bytes32 expectedFinal = keccak256(
            abi.encode(
                "POG_FINAL_EVIDENCE_V1",
                PROJECT_ID,
                PROCUREMENT_ID,
                vendor,
                address(asset),
                RESERVE,
                PO_HASH,
                GRN_HASH,
                INVOICE_HASH,
                INVOICE
            )
        );
        assertEq(registry.getProcurement(PROCUREMENT_ID).finalEvidenceHash, expectedFinal);
        assertEq(registry.computeFinalEvidenceHash(PROCUREMENT_ID), expectedFinal);

        _submitFinalAssessment();
        escrow.markPaymentPending(PROCUREMENT_ID);
        escrow.markPaid(PROCUREMENT_ID, INVOICE);

        PoGRegistry.Procurement memory procurement = registry.getProcurement(PROCUREMENT_ID);
        assertEq(uint8(procurement.status), uint8(PoGRegistry.ProcurementStatus.Paid));
        assertEq(procurement.paidAmount, INVOICE);
        assertEq(registry.paidProcurementCount(PROJECT_ID), 1);
    }

    function test_IllegalTransitionMatrixThroughFinalAssessed() public {
        _createProjectAndProcurement();
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        escrow.markReservePending(PROCUREMENT_ID);

        _recordPreEvidence();
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.recordPrePurchaseEvidence(PROCUREMENT_ID, REQUEST_HASH, QUOTE_HASH);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        escrow.markReservePending(PROCUREMENT_ID);

        _submitPreAssessment();
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);

        escrow.markReservePending(PROCUREMENT_ID);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);

        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.recordDelivery(PROCUREMENT_ID, GRN_HASH);
        vm.prank(foundation);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);

        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, INVOICE);
        vm.prank(foundation);
        registry.recordDelivery(PROCUREMENT_ID, GRN_HASH);

        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        escrow.markPaymentPending(PROCUREMENT_ID);
        vm.prank(foundation);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, INVOICE);

        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        escrow.markPaymentPending(PROCUREMENT_ID);
        _submitFinalAssessment();

        vm.expectPartialRevert(PoGRegistry.WrongState.selector);
        escrow.markPaid(PROCUREMENT_ID, INVOICE);
    }

    function test_CallbacksAreEscrowOnly() public {
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.markReserveApprovalPendingFromEscrow(PROCUREMENT_ID);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.markReservedFromEscrow(PROCUREMENT_ID, RESERVE);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.markPaymentApprovalPendingFromEscrow(PROCUREMENT_ID);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.markPaidFromEscrow(PROCUREMENT_ID, INVOICE);
    }

    function test_CallbackAmountsAreStrict() public {
        _preparePreAssessed();
        escrow.markReservePending(PROCUREMENT_ID);
        vm.expectPartialRevert(PoGRegistry.InvalidReserveAmount.selector);
        escrow.markReserved(PROCUREMENT_ID, 0);
        vm.expectPartialRevert(PoGRegistry.InvalidReserveAmount.selector);
        escrow.markReserved(PROCUREMENT_ID, CAP + 1);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);

        vm.startPrank(foundation);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);
        registry.recordDelivery(PROCUREMENT_ID, GRN_HASH);
        vm.expectPartialRevert(PoGRegistry.InvalidInvoiceAmount.selector);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, RESERVE + 1);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, INVOICE);
        vm.stopPrank();
        _submitFinalAssessment();
        escrow.markPaymentPending(PROCUREMENT_ID);
        vm.expectPartialRevert(PoGRegistry.InvalidPaidAmount.selector);
        escrow.markPaid(PROCUREMENT_ID, INVOICE - 1);
    }

    function test_FinalAssessmentMustRemainCurrentAtPaymentCallbacks() public {
        _prepareInvoiceSubmitted();
        (PoGRegistry.AIAssessment memory assessment,) = _assessment(
            PROCUREMENT_ID,
            PoGRegistry.AssessmentStage.FinalPayment,
            registry.getProcurement(PROCUREMENT_ID).finalEvidenceHash,
            AI_KEY,
            registry.aiNonces(aiSigner)
        );
        assessment.expiresAt = uint64(block.timestamp + 5);
        assessment.assessmentId = registry.computeAssessmentId(assessment);
        (, bytes memory signature) = _sign(assessment, AI_KEY, registry);
        registry.submitAIAssessment(assessment, signature);

        vm.warp(block.timestamp + 6);
        vm.expectPartialRevert(PoGRegistry.AssessmentNotCurrent.selector);
        escrow.markPaymentPending(PROCUREMENT_ID);
    }

    // ---------------------------------------------------------------------
    // Policy, cancellation, roots
    // ---------------------------------------------------------------------

    function test_PolicyUpdateSynchronizesEpochAndThreshold() public {
        _createProject();
        address[] memory approvers = _twoApprovers();

        vm.prank(foundation);
        registry.setApprovalPolicy(PROJECT_ID, approvers, 2);

        PoGRegistry.RegistryProject memory project = registry.getProject(PROJECT_ID);
        assertEq(project.approverEpoch, 2);
        assertEq(project.approvalThreshold, 2);
        assertEq(escrow.epoch(PROJECT_ID), 2);
        assertEq(escrow.threshold(PROJECT_ID), 2);
    }

    function test_PolicyUpdateAuthPendingAndHookAtomicity() public {
        _preparePreAssessed();
        address[] memory approvers = _twoApprovers();

        vm.prank(attacker);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.setApprovalPolicy(PROJECT_ID, approvers, 2);

        escrow.markReservePending(PROCUREMENT_ID);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.PolicyChangeWhileApprovalPending.selector);
        registry.setApprovalPolicy(PROJECT_ID, approvers, 2);

        PoGRegistry fresh = new PoGRegistry(owner);
        MockProcurementEscrow badEscrow = new MockProcurementEscrow(address(fresh));
        vm.prank(owner);
        fresh.bindEscrow(address(badEscrow));
        vm.prank(foundation);
        fresh.createProject(PROJECT_ID, recipient, address(asset), _oneApprover(), 1);
        badEscrow.setReverts(false, true, false);
        vm.prank(foundation);
        vm.expectRevert(MockProcurementEscrow.HookReverted.selector);
        fresh.setApprovalPolicy(PROJECT_ID, approvers, 2);
        assertEq(fresh.getProject(PROJECT_ID).approverEpoch, 1);
        assertEq(fresh.getProject(PROJECT_ID).approvalThreshold, 1);
    }

    function test_CancelBeforeAndAfterReservation() public {
        _createProjectAndProcurement();
        vm.prank(foundation);
        registry.cancelProcurement(PROCUREMENT_ID);
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT_ID).status),
            uint8(PoGRegistry.ProcurementStatus.Cancelled)
        );
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.TerminalState.selector);
        registry.cancelProcurement(PROCUREMENT_ID);

        bytes32 second = keccak256("reserved-cancel");
        vm.prank(foundation);
        registry.createProcurement(second, PROJECT_ID, vendor, CAP);
        _preparePreAssessedFor(second);
        escrow.markReservePending(second);
        escrow.markReserved(second, RESERVE);
        escrow.setReleaseAmount(second, RESERVE);
        vm.prank(foundation);
        registry.cancelProcurement(second);
        assertEq(
            uint8(registry.getProcurement(second).status),
            uint8(PoGRegistry.ProcurementStatus.Cancelled)
        );
    }

    function test_CancellationMismatchOrHookRevertRollsBack() public {
        _preparePreAssessed();
        escrow.markReservePending(PROCUREMENT_ID);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);

        escrow.setReleaseAmount(PROCUREMENT_ID, RESERVE - 1);
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.CancellationReleaseMismatch.selector);
        registry.cancelProcurement(PROCUREMENT_ID);
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT_ID).status),
            uint8(PoGRegistry.ProcurementStatus.BudgetReserved)
        );

        escrow.setReleaseAmount(PROCUREMENT_ID, RESERVE);
        escrow.setReverts(false, false, true);
        vm.prank(foundation);
        vm.expectRevert(MockProcurementEscrow.HookReverted.selector);
        registry.cancelProcurement(PROCUREMENT_ID);
        assertEq(
            uint8(registry.getProcurement(PROCUREMENT_ID).status),
            uint8(PoGRegistry.ProcurementStatus.BudgetReserved)
        );
    }

    function test_AllocationRootPreconditionsAndVersioning() public {
        _createProject();
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.NoPaidProcurement.selector);
        registry.publishAllocationRoot(PROJECT_ID, keccak256("root"), 1);

        _preparePaid();
        vm.prank(attacker);
        vm.expectPartialRevert(PoGRegistry.UnauthorizedCaller.selector);
        registry.publishAllocationRoot(PROJECT_ID, keccak256("root"), 1);

        vm.startPrank(foundation);
        vm.expectPartialRevert(PoGRegistry.ZeroHash.selector);
        registry.publishAllocationRoot(PROJECT_ID, bytes32(0), 1);
        registry.publishAllocationRoot(PROJECT_ID, keccak256("root-1"), 1);
        vm.expectPartialRevert(PoGRegistry.InvalidAllocationRootVersion.selector);
        registry.publishAllocationRoot(PROJECT_ID, keccak256("same-version"), 1);
        registry.publishAllocationRoot(PROJECT_ID, keccak256("root-2"), 2);
        vm.stopPrank();

        PoGRegistry.RegistryProject memory project = registry.getProject(PROJECT_ID);
        assertEq(project.allocationRoot, keccak256("root-2"));
        assertEq(project.allocationRootVersion, 2);
    }

    function test_PaidAndCancelledAreTerminal() public {
        _preparePaid();
        vm.prank(foundation);
        vm.expectPartialRevert(PoGRegistry.TerminalState.selector);
        registry.cancelProcurement(PROCUREMENT_ID);
    }

    // ---------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------

    function _oneApprover() internal view returns (address[] memory approvers) {
        approvers = new address[](1);
        approvers[0] = approver;
    }

    function _twoApprovers() internal view returns (address[] memory approvers) {
        approvers = new address[](2);
        approvers[0] = approver;
        approvers[1] = approverTwo;
    }

    function _createProject() internal {
        if (registry.projectExists(PROJECT_ID)) return;
        vm.prank(foundation);
        registry.createProject(PROJECT_ID, recipient, address(asset), _oneApprover(), 1);
    }

    function _createProjectAndProcurement() internal {
        _createProject();
        if (registry.procurementExists(PROCUREMENT_ID)) return;
        vm.prank(foundation);
        registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);
    }

    function _recordPreEvidence() internal {
        vm.prank(foundation);
        registry.recordPrePurchaseEvidence(PROCUREMENT_ID, REQUEST_HASH, QUOTE_HASH);
    }

    function _preparePreEvidence() internal {
        _createProjectAndProcurement();
        _recordPreEvidence();
    }

    function _submitPreAssessment() internal {
        PoGRegistry.Procurement memory procurement = registry.getProcurement(PROCUREMENT_ID);
        (PoGRegistry.AIAssessment memory assessment, bytes memory signature) = _assessment(
            PROCUREMENT_ID,
            PoGRegistry.AssessmentStage.PreProcurement,
            procurement.preEvidenceHash,
            AI_KEY,
            registry.aiNonces(aiSigner)
        );
        registry.submitAIAssessment(assessment, signature);
    }

    function _preparePreAssessed() internal {
        _preparePreEvidence();
        _submitPreAssessment();
    }

    function _preparePreAssessedFor(bytes32 procurementId) internal {
        vm.prank(foundation);
        registry.recordPrePurchaseEvidence(procurementId, REQUEST_HASH, QUOTE_HASH);
        PoGRegistry.Procurement memory procurement = registry.getProcurement(procurementId);
        (PoGRegistry.AIAssessment memory assessment, bytes memory signature) = _assessment(
            procurementId,
            PoGRegistry.AssessmentStage.PreProcurement,
            procurement.preEvidenceHash,
            AI_KEY,
            registry.aiNonces(aiSigner)
        );
        registry.submitAIAssessment(assessment, signature);
    }

    function _prepareInvoiceSubmitted() internal {
        _preparePreAssessed();
        escrow.markReservePending(PROCUREMENT_ID);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);
        vm.startPrank(foundation);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);
        registry.recordDelivery(PROCUREMENT_ID, GRN_HASH);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, INVOICE);
        vm.stopPrank();
    }

    function _submitFinalAssessment() internal {
        PoGRegistry.Procurement memory procurement = registry.getProcurement(PROCUREMENT_ID);
        (PoGRegistry.AIAssessment memory assessment, bytes memory signature) = _assessment(
            PROCUREMENT_ID,
            PoGRegistry.AssessmentStage.FinalPayment,
            procurement.finalEvidenceHash,
            AI_KEY,
            registry.aiNonces(aiSigner)
        );
        registry.submitAIAssessment(assessment, signature);
    }

    function _preparePaid() internal {
        if (!registry.projectExists(PROJECT_ID)) {
            _createProject();
        }
        if (!registry.procurementExists(PROCUREMENT_ID)) {
            vm.prank(foundation);
            registry.createProcurement(PROCUREMENT_ID, PROJECT_ID, vendor, CAP);
        }
        _recordPreEvidence();
        _submitPreAssessment();
        escrow.markReservePending(PROCUREMENT_ID);
        escrow.markReserved(PROCUREMENT_ID, RESERVE);
        vm.startPrank(foundation);
        registry.recordPurchaseOrder(PROCUREMENT_ID, PO_HASH);
        registry.recordDelivery(PROCUREMENT_ID, GRN_HASH);
        registry.recordInvoice(PROCUREMENT_ID, INVOICE_HASH, INVOICE);
        vm.stopPrank();
        _submitFinalAssessment();
        escrow.markPaymentPending(PROCUREMENT_ID);
        escrow.markPaid(PROCUREMENT_ID, INVOICE);
    }

    function _assessment(
        bytes32 procurementId,
        PoGRegistry.AssessmentStage stage,
        bytes32 evidenceHash,
        uint256 signerKey,
        uint256 nonce
    ) internal view returns (PoGRegistry.AIAssessment memory assessment, bytes memory signature) {
        assessment = PoGRegistry.AIAssessment({
            assessmentId: bytes32(0),
            procurementId: procurementId,
            stage: stage,
            reportHash: keccak256(abi.encode("report", procurementId, stage, nonce)),
            evidenceHash: evidenceHash,
            modelId: keccak256("pog-risk-model-v1"),
            outcome: PoGRegistry.AssessmentOutcome.Review,
            riskScoreBps: 4_200,
            issuedAt: uint64(block.timestamp),
            expiresAt: uint64(block.timestamp + 1 days),
            nonce: nonce,
            aiSigner: vm.addr(signerKey)
        });
        assessment.assessmentId = registry.computeAssessmentId(assessment);
        (, signature) = _sign(assessment, signerKey, registry);
    }

    function _sign(
        PoGRegistry.AIAssessment memory assessment,
        uint256 signerKey,
        PoGRegistry target
    ) internal view returns (bytes32 digest, bytes memory signature) {
        digest = target.hashAIAssessment(assessment);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(signerKey, digest);
        signature = abi.encodePacked(r, s, v);
    }
}
