"""Accepted V2 tail material; explicit developer AI fixtures, not AI detection."""
from eth_utils import keccak
from .typed_data import ai_assessment_typed, human_intent_typed

ZERO = "0x" + "00" * 32


def hex32(value):
    return "0x" + bytes(value).hex()


def tail_material(gate, kind, procurement, project, signer, deadline):
    view = gate.call("PoGRegistryV2", "getProcurement", procurement.business_id)
    now = gate.latest_timestamp()
    if deadline < now:
        raise ValueError("Signing deadline expired")
    registry = gate.contract_address("PoGRegistryV2")
    escrow = gate.contract_address("ProcurementEscrowV2")
    if kind == "ai_final":
        if int(view[-1]) not in {6, 7, 8} or not gate.call("PoGRegistryV2", "aiSigners", signer):
            raise ValueError("Receipt or current AI authority unavailable")
        evidence = hex32(view[14])
        if evidence == ZERO or evidence != hex32(gate.call("PoGRegistryV2", "computeFinalEvidenceHash", procurement.business_id)):
            raise ValueError("Final evidence is not current")
        nonce = int(gate.call("PoGRegistryV2", "aiNonces", signer))
        report = "0x" + keccak(("POG_A3_SYNTHETIC_FINAL_FIXTURE_V1:" + procurement.business_id).encode()).hex()
        provisional = [1, procurement.business_id, ZERO, 0, 100, evidence, report, signer, nonce, deadline]
        message = dict(zip(("stage", "procurementId", "assessmentId", "outcome", "riskScoreBps", "evidenceHash", "reportHash", "signer", "nonce", "deadline"), provisional))
        message["assessmentId"] = hex32(gate.call("PoGRegistryV2", "computeAssessmentId", provisional))
        return ai_assessment_typed(message, 31337, registry), registry, 0, nonce
    if kind not in {"release", "settlement"}:
        raise ValueError("Unsupported tail authorization")
    if int(view[-1]) not in ({7, 8} if kind == "release" else {10, 11}) or int(view[20]) != 0:
        raise ValueError("Current state does not permit this human action")
    ledger = gate.call("ProcurementEscrowV2", "getLedger", project.business_id)
    if not gate.call("ProcurementEscrowV2", "isApprover", project.business_id, signer):
        raise ValueError("Current human policy does not authorize signer")
    assessment_id = ZERO
    if kind == "release":
        assessment = gate.call("PoGRegistryV2", "getAssessment", view[15])
        if not (
            hex32(assessment[0]) == hex32(view[15])
            and hex32(assessment[1]) == procurement.business_id
            and int(assessment[2]) == 1 and hex32(assessment[5]) == hex32(view[14])
            and int(assessment[9]) >= now
            and gate.call("PoGRegistryV2", "aiSigners", assessment[7])
        ):
            raise ValueError("Current FINAL assessment unavailable")
        assessment_id = hex32(view[15])
    elif hex32(view[18]) == ZERO:
        raise ValueError("Settlement evidence is not confirmed")
    nonce = int(gate.call("ProcurementEscrowV2", "humanNonces", signer))
    terms = hex32(gate.call("ProcurementEscrowV2", "releaseTermsHash" if kind == "release" else "settlementTermsHash", procurement.business_id))
    message = {"targetId": procurement.business_id, "action": 1 if kind == "release" else 2,
               "termsHash": terms, "assessmentId": assessment_id, "signer": signer,
               "nonce": nonce, "deadline": deadline, "policyEpoch": int(ledger[9])}
    return human_intent_typed(message, 31337, escrow), escrow, int(ledger[9]), nonce
