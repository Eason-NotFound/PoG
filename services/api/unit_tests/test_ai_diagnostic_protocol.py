"""Synthetic protocol responses verify contracts, never claim a model invocation."""
import base64
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from test_ai_diagnostic_core import env, frozen
from pog_api.ai_diagnostic_core import VERSIONS, request_bytes
from pog_api.ai_protocol.verify_service import CheckFailure, clean_response


def response_fixture(env, frozen):
    value, resources, _, _ = env.build()
    request, _wire = request_bytes(frozen, value, resources, uuid4(), uuid4())
    report = {
        "schemaVersion": "pog.ai.report/0.2-candidate", "stage": 0,
        "projectId": value["registrySnapshot"]["projectId"], "procurementId": value["registrySnapshot"]["procurementId"],
        "evidenceVersion": value["evidenceSnapshot"]["evidenceVersion"], "evidenceHash": value["registrySnapshot"]["currentEvidenceHash"],
        "inputHash": request["inputHash"], "executionMode": "model_and_rules", "versions": VERSIONS.copy(),
        "outcome": 1, "riskScoreBps": None, "completeness": "incomplete",
        "summary": "TEST-ONLY constructed response. No model has been called.",
        "evidenceRefs": deepcopy(value["documents"]), "findings": [],
        "missingInputs": [{"inputKey": key, "reasonCode": "TEST_CONTEXT_MISSING", "requiredForStages": [0]} for key in sorted(
            ["vendorContext.accountHistorySnapshot", "vendorContext.relatedPartySnapshot", "comparisonContext.quoteComparatorsSnapshot",
             "comparisonContext.crossProjectDuplicateSnapshot", "comparisonContext.procurementHistorySnapshot"])],
    }
    raw = frozen.cjson.canonical_bytes(report)
    response = {"schemaVersion": "pog.ai.byte-response/1", **{key: request[key] for key in ("operationId", "aiRequestId", "payloadHash")},
                "status": "completed", "reportBytesBase64": base64.b64encode(raw).decode("ascii"),
                "reportHash": frozen.cjson.keccak256(raw), "error": None, "signingEnvelope": None}
    return value, request, report, response, raw


def test_exact_cjson_response_remains_unsigned_review_null_and_incomplete(env, frozen):
    value, request, report, response, raw = response_fixture(env, frozen)
    returned_raw, returned_report, error = frozen.response(200, response, request, value, VERSIONS)
    assert returned_raw == raw and returned_report == report and error is None
    assert returned_report["riskScoreBps"] is None and returned_report["outcome"] == 1
    assert returned_report["completeness"] == "incomplete" and response["signingEnvelope"] is None


@pytest.mark.parametrize("mutation", ["operation", "request", "payload", "hash", "signature", "input_binding", "project_binding",
                                    "evidence_binding", "version", "missing_context", "risk_score", "partial_refs", "document_hash", "noncanonical", "stage"])
def test_provider_poisoned_response_is_rejected_before_storage(env, frozen, mutation):
    value, request, report, response, _raw = response_fixture(env, frozen)
    if mutation in {"operation", "request", "payload", "hash"}:
        field = {"operation": "operationId", "request": "aiRequestId", "payload": "payloadHash", "hash": "reportHash"}[mutation]
        response[field] = str(uuid4()) if mutation in {"operation", "request"} else "0x" + "ff" * 32
    elif mutation == "signature":
        response["signingEnvelope"] = {"signature": "not-authorized"}
    else:
        if mutation == "input_binding":
            report["inputHash"] = "0x" + "ff" * 32
        elif mutation == "project_binding":
            report["projectId"] = "0x" + "ff" * 32
        elif mutation == "evidence_binding":
            report["evidenceVersion"] = "2"
        elif mutation == "version":
            report["versions"]["modelId"] = "unapproved-model"
        elif mutation == "missing_context":
            report["missingInputs"] = report["missingInputs"][:-1]
        elif mutation == "risk_score":
            report["riskScoreBps"] = 0
        elif mutation == "partial_refs":
            report["evidenceRefs"] = report["evidenceRefs"][:-1]
        elif mutation == "document_hash":
            report["evidenceRefs"][0]["contentSha256"] = "0x" + "ff" * 32
        elif mutation == "stage":
            report["stage"] = 1
        raw = frozen.cjson.canonical_bytes(report)
        if mutation == "noncanonical":
            raw += b"\n"
        response["reportHash"] = frozen.cjson.keccak256(raw)
        response["reportBytesBase64"] = base64.b64encode(raw).decode("ascii")
    with pytest.raises(CheckFailure):
        frozen.response(200, response, request, value, VERSIONS)


def test_bearer_echo_in_raw_nested_strings_or_report_is_never_persisted(env, frozen):
    token = b"TEST_ONLY_PRIVATE_TOKEN_0123456789"
    for parsed in ({"message": token.decode()}, {"nested": [token.decode()]}):
        raw = json.dumps(parsed).encode()
        with pytest.raises(CheckFailure):
            clean_response(raw, parsed, token)
    _value, _request, report, response, _raw = response_fixture(env, frozen)
    report["summary"] = "provider echoed " + token.decode()
    response["reportBytesBase64"] = base64.b64encode(frozen.cjson.canonical_bytes(report)).decode()
    raw = json.dumps(response).encode()
    assert token not in raw
    with pytest.raises(CheckFailure):
        clean_response(raw, response, token)
