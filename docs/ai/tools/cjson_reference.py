#!/usr/bin/env python3
"""Offline PoG-CJSON-0.2 reference, NOT a production adapter or signing service.

Only ReportBody gets semantic validation here. ``input``/``generic`` validate
raw JSON/scalars/limits; validate inputSnapshot against its separate schema too.
No environment lookup, networking, keys, model, chain or signature operations.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

PROFILE = "PoG-CJSON-0.2"
BODY_VERSION = "pog.ai.report/0.2-candidate"
HEX32 = re.compile(r"0x[0-9a-f]{64}\Z", re.ASCII)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z", re.ASCII)
DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z", re.ASCII)
MAX_CANONICAL = 1_048_576
MAX_RAW = 4 * MAX_CANONICAL


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ValidationError(code, message)


def load_raw(raw: bytes):
    require(len(raw) <= MAX_RAW, "RAW_LIMIT", "raw JSON exceeds reference limit")
    require(not raw.startswith(b"\xef\xbb\xbf"), "BOM", "UTF-8 BOM forbidden")
    try:
        source = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("INVALID_UTF8", str(exc)) from exc

    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, "DUPLICATE_KEY", f"decoded duplicate key: {key!r}")
            result[key] = value
        return result

    def integer(token):
        require(bool(DECIMAL.fullmatch(token)), "NUMBER_TOKEN", "unsigned decimal integer tokens only")
        require(len(token) <= 10 and int(token) <= 2_147_483_647,
                "INTEGER_RANGE", "wide integers must use decimal strings")
        return int(token)

    def forbidden(token):
        raise ValidationError("NUMBER_TOKEN", f"forbidden number token: {token}")

    try:
        value = json.loads(source, object_pairs_hook=pairs, parse_int=integer,
                           parse_float=forbidden, parse_constant=forbidden)
    except ValidationError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ValidationError("JSON_SYNTAX", str(exc)) from exc
    validate_tree(value)
    return value


def validate_tree(value, depth=1):
    require(depth <= 32, "DEPTH_LIMIT", "maximum nesting depth is 32")
    if isinstance(value, str):
        require(len(value) <= 8192, "STRING_LIMIT", "maximum string length is 8192 scalars")
        require(all(not 0xD800 <= ord(c) <= 0xDFFF for c in value),
                "INVALID_UNICODE", "isolated surrogate forbidden")
    elif type(value) is int:
        require(0 <= value <= 2_147_483_647, "INTEGER_RANGE", "small unsigned integers only")
    elif value is None or type(value) is bool:
        pass
    elif isinstance(value, list):
        require(len(value) <= 1024, "ARRAY_LIMIT", "maximum array length is 1024")
        for item in value:
            validate_tree(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str) and key.isascii(), "OBJECT_KEY", "object keys must be ASCII")
            validate_tree(key, depth + 1)
            validate_tree(item, depth + 1)
    else:
        raise ValidationError("JSON_TYPE", "only JSON scalar/container values allowed")


def keys(value, expected, path):
    require(isinstance(value, dict) and set(value) == set(expected),
            "BODY_SCHEMA", f"{path}: exact keys required: {', '.join(expected)}")


def string(value, path, identifier=False, nullable=False):
    if nullable and value is None:
        return
    require(isinstance(value, str), "BODY_SCHEMA", f"{path}: string required")
    if identifier:
        require(bool(IDENTIFIER.fullmatch(value)), "BODY_SCHEMA", f"{path}: ASCII machine identifier required")


def uint_string(value, bits, path):
    require(isinstance(value, str) and bool(DECIMAL.fullmatch(value)),
            "BODY_SCHEMA", f"{path}: canonical decimal string required")
    require(len(value) <= len(str((1 << bits) - 1)) and int(value) < 1 << bits,
            "WIDE_RANGE", f"{path}: uint{bits} overflow")


def small_integer(value, minimum, maximum, path, nullable=False):
    if nullable and value is None:
        return
    require(type(value) is int and minimum <= value <= maximum,
            "BODY_SCHEMA", f"{path}: integer {minimum}..{maximum} required; bool is not integer")


def array(value, path):
    require(isinstance(value, list), "BODY_SCHEMA", f"{path}: array required")


def ordered(values, path):
    require(all(a < b for a, b in zip(values, values[1:])), "ARRAY_ORDER",
            f"{path}: strictly increasing order, no duplicates")


def validate_report(body):
    keys(body, ["schemaVersion", "stage", "projectId", "procurementId", "evidenceVersion",
                "evidenceHash", "inputHash", "executionMode", "versions", "outcome",
                "riskScoreBps", "completeness", "summary", "evidenceRefs", "findings", "missingInputs"], "BODY")
    require(body["schemaVersion"] == BODY_VERSION, "BODY_SCHEMA", "wrong ReportBody schemaVersion")
    small_integer(body["stage"], 0, 1, "stage")
    small_integer(body["outcome"], 0, 2, "outcome")
    small_integer(body["riskScoreBps"], 0, 10000, "riskScoreBps", nullable=True)
    for key in ("projectId", "procurementId", "evidenceHash", "inputHash"):
        require(isinstance(body[key], str) and bool(HEX32.fullmatch(body[key])), "BODY_SCHEMA", f"{key}: lowercase bytes32 required")
    uint_string(body["evidenceVersion"], 64, "evidenceVersion")
    require(body["executionMode"] in ("synthetic_fixture", "rules_only", "model_and_rules"), "BODY_SCHEMA", "invalid executionMode")
    require(body["completeness"] in ("complete", "incomplete"), "BODY_SCHEMA", "invalid completeness")
    string(body["summary"], "summary")
    version = body["versions"]
    version_keys = ["serviceVersion", "ruleSetVersion", "extractionSchemaVersion", "scorePolicyVersion", "modelProvider", "modelId", "modelVersion", "promptVersion"]
    keys(version, version_keys, "versions")
    for key in version_keys:
        value = version[key]
        require(value is None or isinstance(value, str) and len(value) > 0,
                "BODY_SCHEMA", f"versions.{key}: nonempty string or null")
    for key in version_keys[:3]:
        require(version[key] is not None, "BODY_SCHEMA", f"versions.{key}: nonempty string required")
    require(version["scorePolicyVersion"] is not None or body["riskScoreBps"] is None,
            "BODY_SCHEMA", "unknown scoring policy requires null risk score")
    model_keys = version_keys[4:]
    if body["executionMode"] == "model_and_rules":
        require(all(version[k] is not None for k in model_keys), "BODY_SCHEMA", "model mode requires complete model/prompt versions")
    else:
        require(all(version[k] is None for k in model_keys), "BODY_SCHEMA", "non-model mode requires null model/prompt versions")

    refs = body["evidenceRefs"]
    array(refs, "evidenceRefs")
    ref_by_id = {}
    for ref in refs:
        keys(ref, ["evidenceId", "kind", "version", "contentSha256", "leafKeccak256", "pages"], "EvidenceRef")
        string(ref["evidenceId"], "evidenceId", identifier=True)
        require(ref["evidenceId"] not in ref_by_id, "BODY_SCHEMA", "evidenceId must be unique")
        ref_by_id[ref["evidenceId"]] = ref
        require(ref["kind"] in ("ProcurementRequest", "Quote", "PO", "GRN", "Invoice", "Receipt", "Photo", "Inspection"), "BODY_SCHEMA", "unknown EvidenceRef.kind")
        uint_string(ref["version"], 64, "EvidenceRef.version")
        for key in ("contentSha256", "leafKeccak256"):
            require(isinstance(ref[key], str) and bool(HEX32.fullmatch(ref[key])), "BODY_SCHEMA", "EvidenceRef hash must be lowercase bytes32")
        # Business kind alone does not determine MIME: Invoice may be an image.
        require(ref["kind"] != "Photo" or ref["pages"] is None,
                "BODY_SCHEMA", "Photo requires pages=null")
        if ref["pages"] is not None:
            array(ref["pages"], "pages")
            for page in ref["pages"]:
                small_integer(page, 1, 2_147_483_647, "page")
            ordered(ref["pages"], "pages")
    ordered([(r["evidenceId"], int(r["version"])) for r in refs], "evidenceRefs")

    findings = body["findings"]
    array(findings, "findings")
    seen_findings = set()
    for finding in findings:
        keys(finding, ["findingId", "reasonCode", "severity", "message", "evidenceLocators"], "Finding")
        for key in ("findingId", "reasonCode"):
            string(finding[key], key, identifier=True)
        require(finding["findingId"] not in seen_findings, "BODY_SCHEMA", "findingId must be unique")
        seen_findings.add(finding["findingId"])
        require(finding["severity"] in ("information", "review"), "BODY_SCHEMA", "invalid severity")
        string(finding["message"], "message")
        array(finding["evidenceLocators"], "evidenceLocators")
        sort_keys = []
        for loc in finding["evidenceLocators"]:
            keys(loc, ["evidenceId", "page", "field"], "Locator")
            string(loc["evidenceId"], "Locator.evidenceId", identifier=True)
            require(loc["evidenceId"] in ref_by_id, "BODY_SCHEMA", "locator references unknown evidence")
            small_integer(loc["page"], 1, 2_147_483_647, "Locator.page", nullable=True)
            if loc["page"] is not None:
                pages = ref_by_id[loc["evidenceId"]]["pages"]
                require(pages is not None and loc["page"] in pages,
                        "BODY_SCHEMA", "locator page not in reference pages")
            string(loc["field"], "Locator.field", identifier=True, nullable=True)
            sort_keys.append((loc["evidenceId"], -1 if loc["page"] is None else loc["page"], "" if loc["field"] is None else loc["field"]))
        ordered(sort_keys, "evidenceLocators")
    ordered([(f["reasonCode"], f["findingId"]) for f in findings], "findings")

    missing = body["missingInputs"]
    array(missing, "missingInputs")
    for item in missing:
        keys(item, ["inputKey", "reasonCode", "requiredForStages"], "MissingInput")
        for key in ("inputKey", "reasonCode"):
            string(item[key], key, identifier=True)
        array(item["requiredForStages"], "requiredForStages")
        require(len(item["requiredForStages"]) > 0, "BODY_SCHEMA", "requiredForStages must be nonempty")
        for stage in item["requiredForStages"]:
            small_integer(stage, 0, 1, "requiredForStages")
        ordered(item["requiredForStages"], "requiredForStages")
    ordered([item["inputKey"] for item in missing], "missingInputs")
    if body["completeness"] == "incomplete":
        require(body["outcome"] == 1 and body["riskScoreBps"] is None and len(missing) > 0,
                "BODY_SCHEMA", "incomplete requires Review/null and explicit missingInputs")
    else:
        require(not missing, "BODY_SCHEMA", "complete requires missingInputs=[]")
    if body["riskScoreBps"] is None:
        require(body["outcome"] == 1, "BODY_SCHEMA", "unknown score cannot imply Pass or Reject")


def escape_string(value):
    out = ['"']
    for char in value:
        number = ord(char)
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif number < 32:
            out.append(f"\\u{number:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def canonical_bytes(value):
    validate_tree(value)

    def encode(item):
        if item is None:
            return "null"
        if type(item) is bool:
            return "true" if item else "false"
        if type(item) is int:
            return str(item)
        if isinstance(item, str):
            return escape_string(item)
        if isinstance(item, list):
            return "[" + ",".join(encode(x) for x in item) + "]"
        return "{" + ",".join(escape_string(k) + ":" + encode(item[k]) for k in sorted(item)) + "}"

    result = encode(value).encode("utf-8", errors="strict")
    require(len(result) <= MAX_CANONICAL, "BODY_LIMIT", "canonical object exceeds 1 MiB")
    return result


# Independent offline Keccak implementation; suffix 0x01 is Ethereum Keccak,
# NOT FIPS SHA3's 0x06. Candidate only, checked against published empty/abc KATs.
MASK = (1 << 64) - 1
ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
ROTATION = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2),
            (62, 6, 43, 15, 61), (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))


def rotate(value, shift):
    return ((value << shift) | (value >> (64 - shift))) & MASK


def keccak256(raw: bytes) -> str:
    rate = 136
    padded = bytearray(raw)
    padded.append(1)
    padded.extend(b"\x00" * ((-len(padded)) % rate))
    padded[-1] |= 128
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        for lane in range(rate // 8):
            state[lane % 5][lane // 5] ^= int.from_bytes(padded[offset + lane * 8:offset + lane * 8 + 8], "little")
        for constant in ROUND_CONSTANTS:
            columns = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
            diff = [columns[(x - 1) % 5] ^ rotate(columns[(x + 1) % 5], 1) for x in range(5)]
            moved = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    moved[y][(2 * x + 3 * y) % 5] = rotate(state[x][y] ^ diff[x], ROTATION[x][y])
            for x in range(5):
                for y in range(5):
                    state[x][y] = moved[x][y] ^ ((~moved[(x + 1) % 5][y]) & moved[(x + 2) % 5][y])
            state[0][0] ^= constant
    output = b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return "0x" + output.hex()


def encode_file(path: Path, kind: str):
    obj = load_raw(path.read_bytes())
    if kind == "report":
        validate_report(obj)
    raw = canonical_bytes(obj)
    return obj, raw, keccak256(raw)


def verify_manifest(path: Path):
    manifest = load_raw(path.read_bytes())
    require(manifest["profile"] == PROFILE, "MANIFEST", "wrong profile")
    successes = failures = 0
    for item in manifest["positiveVectors"]:
        try:
            obj, raw, digest = encode_file(path.parent / item["source"], item["kind"])
            require(raw == (path.parent / item["canonicalUtf8"]).read_bytes(), "BYTES_MISMATCH", item["id"])
            require(raw.hex() == (path.parent / item["canonicalHex"]).read_text("ascii").strip(), "HEX_MISMATCH", item["id"])
            require(len(raw) == item["canonicalLength"] and digest == item["expectedKeccak256"], "HASH_MISMATCH", item["id"])
            if item.get("inputSource"):
                _, _, input_hash = encode_file(path.parent / item["inputSource"], "input")
                require(obj["inputHash"] == input_hash, "INPUT_BINDING", item["id"])
            successes += 1
        except (ValidationError, OSError) as exc:
            failures += 1
            print(f"FAIL {item['id']}: {exc}", file=sys.stderr)
    for item in manifest["negativeVectors"]:
        try:
            encode_file(path.parent / item["source"], item["kind"])
            raise AssertionError("invalid source accepted")
        except ValidationError as exc:
            if exc.code == item["expectedError"]:
                successes += 1
            else:
                failures += 1
                print(f"FAIL {item['id']}: expected {item['expectedError']}, got {exc.code}", file=sys.stderr)
        except (OSError, AssertionError) as exc:
            failures += 1
            print(f"FAIL {item['id']}: {exc}", file=sys.stderr)
    for relation in manifest["hashRelations"]:
        values = {v["id"]: v["expectedKeccak256"] for v in manifest["positiveVectors"]}
        equal = values[relation["left"]] == values[relation["right"]]
        if equal == (relation["relation"] == "equal"):
            successes += 1
        else:
            failures += 1
    result = {"implementation": "python", "profile": PROFILE, "pass": successes,
              "fail": failures, "skip": 0, "independentAcceptance": "NOT_RUN"}
    print(json.dumps(result))
    return failures


def selftest():
    vectors = [(b"", "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
               (b"abc", "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")]
    for raw, expected in vectors:
        require(keccak256(raw) == expected, "KAT_FAILURE", "Ethereum Keccak KAT mismatch")
    print(json.dumps({"implementation": "python", "keccakKnownAnswerTests": 2, "pass": 2, "fail": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("encode")
    encode.add_argument("source", type=Path)
    encode.add_argument("--kind", choices=("report", "input", "generic"), default="report")
    encode.add_argument("--out", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    commands.add_parser("selftest")
    args = parser.parse_args()
    try:
        if args.command == "encode":
            _, raw, digest = encode_file(args.source, args.kind)
            if args.out:
                args.out.write_bytes(raw)
            print(json.dumps({"profile": PROFILE, "kind": args.kind, "canonicalLength": len(raw),
                              "keccak256": digest, "semanticValidation": "ReportBody" if args.kind == "report" else "separate input schema required"}))
        elif args.command == "verify":
            return int(bool(verify_manifest(args.manifest)))
        else:
            selftest()
    except (ValidationError, OSError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "FILE_ERROR"), "message": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
