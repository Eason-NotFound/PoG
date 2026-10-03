#!/usr/bin/env python3
"""Regenerate unsigned candidate fixtures from the two reviewed example bundles.

Generation uses Python. Acceptance MUST also run the independent strict TypeScript encoder,
third-party Keccak and separate schema checks. This does not freeze a profile.
"""
import copy
import hashlib
import json
from pathlib import Path

from cjson_reference import canonical_bytes, keccak256, load_raw, validate_report

AI_ROOT = Path(__file__).resolve().parents[1]
VECTOR_ROOT = AI_ROOT / "vectors" / "v2"


def pretty(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main():
    VECTOR_ROOT.mkdir(parents=True, exist_ok=True)
    (VECTOR_ROOT / "positive").mkdir(exist_ok=True)
    (VECTOR_ROOT / "negative").mkdir(exist_ok=True)
    (VECTOR_ROOT / "dependency-data").mkdir(exist_ok=True)
    manifest = {
        "schemaVersion": "pog.ai.cjson-vectors/0.2-candidate",
        "profile": "PoG-CJSON-0.2",
        "status": "CANDIDATE_API_REVIEW_PENDING_NOT_FROZEN",
        "fixtureSafety": {
            "classification": "offline_synthetic_byte_hash_fixture_only",
            "modelExecuted": False,
            "chainVerified": False,
            "productionSigningAllowed": False,
            "signedProtocolPositive": "NOT_RUN",
            "independentAcceptance": "NOT_RUN",
            "scoreMeaning": "1000 is a fixed serialization test value, not a model or approved scoring policy",
        },
        "positiveVectors": [],
        "negativeVectors": [],
        "hashRelations": [],
        "dependencyFiles": [],
    }

    def positive(name, obj, kind="report", features=None, input_source=None, source_raw=None):
        if kind == "report":
            validate_report(obj)
        source = f"positive/{name}.json"
        canonical = f"positive/{name}.cjson"
        hex_path = f"positive/{name}.hex"
        raw = canonical_bytes(obj)
        (VECTOR_ROOT / source).write_bytes(source_raw if source_raw is not None else pretty(obj))
        (VECTOR_ROOT / canonical).write_bytes(raw)
        (VECTOR_ROOT / hex_path).write_text(raw.hex() + "\n", encoding="ascii", newline="\n")
        item = {"id": name, "kind": kind, "source": source,
                "canonicalUtf8": canonical, "canonicalHex": hex_path,
                "canonicalLength": len(raw), "expectedKeccak256": keccak256(raw),
                "features": features or []}
        if input_source:
            item["inputSource"] = input_source
        manifest["positiveVectors"].append(item)
        return source

    def negative(name, raw_or_obj, code, kind="report"):
        source = f"negative/{name}.json"
        raw = raw_or_obj if isinstance(raw_or_obj, bytes) else pretty(raw_or_obj)
        (VECTOR_ROOT / source).write_bytes(raw)
        manifest["negativeVectors"].append({"id": name, "source": source, "kind": kind, "expectedError": code})

    incomplete = {}
    complete = {}
    for stage, filename in [(0, "prepurchase"), (1, "final-release")]:
        bundle = load_raw((AI_ROOT / "examples" / "v2" / f"{filename}.candidate.json").read_bytes())
        input_obj = copy.deepcopy(bundle["inputCandidate"])
        report = copy.deepcopy(bundle["reportCandidate"])
        assert report["inputHash"] == keccak256(canonical_bytes(input_obj))
        input_source = positive(f"stage-{stage}-input-incomplete", input_obj, "input", ["full immutable input", "synthetic unverified chain"])
        positive(f"stage-{stage}-report-incomplete", report, features=["Review", "null risk", "explicit missingInputs", "empty model versions"], input_source=input_source)
        incomplete[stage] = report
        # Standalone business fixture supplies all missing dependency snapshots.
        # Facts are synthetic presets, with verified=false and no production sign.
        complete_input = copy.deepcopy(input_obj)
        complete_input["evidenceSnapshot"]["snapshotId"] = f"synthetic-stage-{stage}-complete-v1"
        complete_input["evidenceSnapshot"]["evidenceVersion"] = str(stage + 10)
        complete_input["vendorContext"]["identityStatus"] = "verified_synthetic_preset"
        external = []
        for group, fields in [
            ("vendorContext", ["accountHistorySnapshot", "relatedPartySnapshot"]),
            ("comparisonContext", ["quoteComparatorsSnapshot", "crossProjectDuplicateSnapshot", "procurementHistorySnapshot"]),
        ]:
            for field in fields:
                sid = f"synthetic-stage-{stage}-{field}-v1"
                dep_source = f"dependency-data/{sid}.json"
                dep_raw = pretty({"classification": "synthetic_dependency_preset", "snapshotId": sid,
                                  "version": "synthetic-v1", "records": [],
                                  "meaning": "Empty authoritative fixture data only; not a real vendor/history query"})
                (VECTOR_ROOT / dep_source).write_bytes(dep_raw)
                snap = {"snapshotId": sid, "provider": "synthetic_fixture", "version": "synthetic-v1",
                        "contentSha256": "0x" + hashlib.sha256(dep_raw).hexdigest(),
                        "contentKeccak256": keccak256(dep_raw)}
                external.append(snap)
                complete_input[group][field] = sid
                manifest["dependencyFiles"].append({"snapshotId": sid, "source": dep_source,
                    "contentSha256": snap["contentSha256"], "contentKeccak256": snap["contentKeccak256"],
                    "byteLength": len(dep_raw)})
        complete_input["evidenceSnapshot"]["externalSnapshots"] = sorted(external, key=lambda x: x["snapshotId"])
        complete_input_source = positive(f"stage-{stage}-input-complete", complete_input, "input",
            ["all required dependency snapshots supplied as synthetic presets", "chain verified=false", "no model or production signing"])
        scored = copy.deepcopy(report)
        scored["inputHash"] = keccak256(canonical_bytes(complete_input))
        scored["evidenceVersion"] = complete_input["evidenceSnapshot"]["evidenceVersion"]
        scored["completeness"] = "complete"
        scored["missingInputs"] = []
        scored["outcome"] = 1
        scored["riskScoreBps"] = 1000
        scored["versions"]["scorePolicyVersion"] = "serialization-test-score-v1"
        scored["summary"] = f"Synthetic stage {stage} complete-input fixture; 1000 is a serialization test value. No live chain, model or production signing validation."
        scored["findings"] = []
        positive(f"stage-{stage}-report-scored", scored,
                 features=["complete input and BODY fixture preset", "test risk=1000", "synthetic_fixture", "not a production validated assessment"],
                 input_source=complete_input_source)
        complete[stage] = scored

    base = copy.deepcopy(complete[0])
    edge = copy.deepcopy(base)
    edge["summary"] = "中文😀 NFC:\u00e9 NFD:e\u0301 line:\u2028paragraph:\u2029 quote:\" slash:/ backslash:\\ html:<>& controls:" + "".join(chr(x) for x in range(32))
    edge["evidenceRefs"][0]["pages"] = None
    edge["evidenceRefs"][0]["kind"] = "Photo"
    edge["findings"] = [{"findingId": "f001", "reasonCode": "SERIALIZATION_TEST", "severity": "information", "message": "Non-paged evidence test", "evidenceLocators": [{"evidenceId": edge["evidenceRefs"][0]["evidenceId"], "page": None, "field": None}]}]
    positive("unicode-controls-empty-null", edge, features=["Chinese", "emoji", "NFC/NFD in one string", "U+2028", "U+2029", "all controls U+0000..001F", "quotes", "slash", "backslash", "HTML characters", "nonpaged image pages=null", "Locator.page=null", "Locator.field=null"])
    escaped_source = (json.dumps(edge, ensure_ascii=True, indent=1) + "\n").encode("ascii")
    positive("unicode-source-escape-equivalent", edge, features=["surrogate pair JSON escape decodes to emoji", "escaped source canonicalizes to same scalars"], source_raw=escaped_source)
    manifest["hashRelations"].append({"left": "unicode-controls-empty-null", "right": "unicode-source-escape-equivalent", "relation": "equal"})
    image = copy.deepcopy(base)
    image["evidenceRefs"][0]["kind"] = "Invoice"
    image["evidenceRefs"][0]["pages"] = None
    image["findings"] = [{"findingId": "f001", "reasonCode": "SERIALIZATION_TEST", "severity": "information",
        "message": "Image Invoice; no page numbering", "evidenceLocators": [{
            "evidenceId": image["evidenceRefs"][0]["evidenceId"], "page": None, "field": None}]}]
    positive("invoice-image-null-pages", image, features=["nonpaged Invoice pages=null", "Locator.page=null", "kind does not imply PDF MIME; trusted input linking separately required"])
    pdf = copy.deepcopy(image)
    pdf["evidenceRefs"][0]["pages"] = []
    positive("invoice-pdf-no-selected-pages", pdf, features=["PDF report pages=[] means no selected page, never nonpaged", "no numeric locator", "trusted input MIME linking separately required"])
    manifest["hashRelations"].append({"left": "invoice-image-null-pages", "right": "invoice-pdf-no-selected-pages", "relation": "different"})
    for name, text in [("unicode-nfc", "\u00e9"), ("unicode-nfd", "e\u0301")]:
        obj = copy.deepcopy(base); obj["summary"] = text
        positive(name, obj, features=["no implicit Unicode normalization"])
    manifest["hashRelations"].append({"left": "unicode-nfc", "right": "unicode-nfd", "relation": "different"})

    def reverse_keys(obj):
        if isinstance(obj, dict):
            return {k: reverse_keys(v) for k, v in reversed(list(obj.items()))}
        if isinstance(obj, list):
            return [reverse_keys(x) for x in obj]
        return obj

    positive("object-keys-reordered", reverse_keys(base), features=["recursive source object order is ignored"])
    manifest["hashRelations"].append({"left": "stage-0-report-scored", "right": "object-keys-reordered", "relation": "equal"})
    wide = copy.deepcopy(base); wide["evidenceVersion"] = str(2**64 - 1)
    wide["evidenceRefs"][0]["version"] = str(2**64 - 1)
    positive("uint64-maximum", wide, features=["uint64 max is decimal string, never float"])
    for n in [133, 134, 135, 269]:
        positive(f"keccak-rate-{n + 2}-bytes", "a" * n, "generic", ["Keccak rate/padding boundary"])
    positive("array-order-first", {"values": [1, 2]}, "generic", ["generic arrays preserve source order"])
    positive("array-order-second", {"values": [2, 1]}, "generic", ["generic array hash differs; BODY semantic arrays must separately satisfy prescribed order"])
    manifest["hashRelations"].append({"left": "array-order-first", "right": "array-order-second", "relation": "different"})

    negative("raw-duplicate-key", b'{"stage":0,"stage":1}', "DUPLICATE_KEY", "generic")
    negative("escaped-duplicate-key", b'{"stage":0,"st\\u0061ge":1}', "DUPLICATE_KEY", "generic")
    negative("utf8-bom", b"\xef\xbb\xbf" + pretty(base), "BOM")
    negative("invalid-utf8", b'{"value":"\xff"}', "INVALID_UTF8", "generic")
    negative("isolated-surrogate", b'{"value":"\\ud800"}', "INVALID_UNICODE", "generic")
    base_raw = pretty(base)
    for name, token in [("negative-zero", b"-0"), ("exponent", b"1e3"), ("float", b"1000.0"), ("nan", b"NaN"), ("infinity", b"Infinity")]:
        negative(name, base_raw.replace(b'"riskScoreBps": 1000', b'"riskScoreBps": ' + token), "NUMBER_TOKEN")
    negative("small-integer-overflow", b'{"value":2147483648}', "INTEGER_RANGE", "generic")

    def changed(name, change, code="BODY_SCHEMA", template=None):
        obj = copy.deepcopy(base if template is None else template); change(obj); negative(name, obj, code)

    changed("image-numeric-locator", lambda x: x["findings"][0]["evidenceLocators"][0].update(page=1), template=image)
    changed("pdf-unselected-numeric-locator", lambda x: x["findings"][0]["evidenceLocators"][0].update(page=1), template=pdf)
    changed("photo-pages-empty-array", lambda x: x["evidenceRefs"][0].update(pages=[]), template=edge)
    changed("photo-pages-numbered", lambda x: x["evidenceRefs"][0].update(pages=[1]), template=edge)
    changed("unknown-evidence-kind", lambda x: x["evidenceRefs"][0].update(kind="UnspecifiedImage"))
    changed("unknown-execution-mode", lambda x: x.update(executionMode="assumed_model_success"))
    changed("boolean-as-stage-integer", lambda x: x.update(stage=True))
    changed("uint64-overflow", lambda x: x.update(evidenceVersion=str(2**64)), "WIDE_RANGE")
    changed("uint64-leading-zero", lambda x: x.update(evidenceVersion="01"))
    changed("uint64-plus-sign", lambda x: x.update(evidenceVersion="+1"))
    changed("evidence-refs-unsorted", lambda x: x["evidenceRefs"].reverse(), "ARRAY_ORDER")
    changed("pages-unsorted", lambda x: x["evidenceRefs"][0].update(pages=[2, 1]), "ARRAY_ORDER")
    changed("pages-duplicate", lambda x: x["evidenceRefs"][0].update(pages=[1, 1]), "ARRAY_ORDER")
    changed("boolean-as-page-integer", lambda x: x["evidenceRefs"][0].update(pages=[True]))
    changed("findings-unsorted", lambda x: x.update(findings=[
        {"findingId": "f2", "reasonCode": "Z_TEST", "severity": "information", "message": "test", "evidenceLocators": []},
        {"findingId": "f1", "reasonCode": "A_TEST", "severity": "information", "message": "test", "evidenceLocators": []}]), "ARRAY_ORDER")
    changed("locators-unsorted", lambda x: x.update(findings=[
        {"findingId": "f1", "reasonCode": "TEST", "severity": "information", "message": "test", "evidenceLocators": [
            {"evidenceId": x["evidenceRefs"][1]["evidenceId"], "page": 1, "field": "quantity"},
            {"evidenceId": x["evidenceRefs"][0]["evidenceId"], "page": 1, "field": "quantity"}]}]), "ARRAY_ORDER")
    changed("missing-inputs-unsorted", lambda x: x["missingInputs"].reverse(), "ARRAY_ORDER", incomplete[0])
    changed("required-stages-unsorted", lambda x: x["missingInputs"][0].update(requiredForStages=[1, 0]), "ARRAY_ORDER", incomplete[0])
    changed("unknown-body-key", lambda x: x.update(reportHash="0x" + "0" * 64))
    changed("missing-input-hash", lambda x: x.pop("inputHash"))
    changed("null-risk-pass", lambda x: x.update(riskScoreBps=None, outcome=0))
    changed("incomplete-pass", lambda x: x.update(outcome=0), template=incomplete[0])
    changed("incomplete-scored", lambda x: x.update(riskScoreBps=1000), template=incomplete[0])
    changed("complete-has-missing", lambda x: x.update(completeness="complete"), template=incomplete[0])
    changed("model-mode-missing-versions", lambda x: x.update(executionMode="model_and_rules"))
    changed("non-model-has-model-version", lambda x: x["versions"].update(modelId="invented-model"))
    changed("uppercase-hash", lambda x: x.update(inputHash="0x" + "A" * 64))
    changed("risk-score-out-of-range", lambda x: x.update(riskScoreBps=10001))
    changed("machine-id-trailing-lf", lambda x: x["evidenceRefs"][0].update(evidenceId=x["evidenceRefs"][0]["evidenceId"] + "\n"))
    changed("hash-trailing-lf", lambda x: x.update(inputHash=x["inputHash"] + "\n"))
    changed("uint-string-trailing-lf", lambda x: x.update(evidenceVersion="1\n"))
    changed("required-stages-empty", lambda x: x["missingInputs"][0].update(requiredForStages=[]), template=incomplete[0])
    changed("score-without-policy", lambda x: x["versions"].update(scorePolicyVersion=None))
    negative("non-ascii-object-key", {"中文": "test"}, "OBJECT_KEY", "generic")
    negative("array-limit", {"values": [0] * 1025}, "ARRAY_LIMIT", "generic")
    negative("string-limit", {"value": "a" * 8193}, "STRING_LIMIT", "generic")
    nested = None
    for _ in range(33):
        nested = [nested]
    negative("depth-limit", nested, "DEPTH_LIMIT", "generic")
    (VECTOR_ROOT / "manifest.json").write_bytes(pretty(manifest))
    print(json.dumps({"positiveVectors": len(manifest["positiveVectors"]), "negativeVectors": len(manifest["negativeVectors"]), "hashRelations": len(manifest["hashRelations"]), "status": manifest["status"]}))


if __name__ == "__main__":
    main()
