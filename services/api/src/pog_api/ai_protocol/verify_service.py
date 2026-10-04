#!/usr/bin/env python3
"""Private, one-attempt Mac acceptance client; no installation or retry."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

FROZEN_COMMIT = "d74a7a0ef53abe32640f019ce1551bf5c2432826"
APPROVED_MAC_REQUEST_SHA256 = "61ceee2d5ba33f17c4a922a21636250a538f5fac45d9f4ebc78a30d9a59a3dc0"
FROZEN_FILES = {
    "docs/ai/tools/cjson_reference.py": "065340ae7135a3f051a3f0584f557576e63ca50619de165f6b754315b6b24be0",
    "docs/ai/schemas/v2/common.schema.json": "4b85e41aae1f6fe7e6c2c9274d377408384b1165beab970d1e04bcdd7ff342fd",
    "docs/ai/schemas/v2/input.schema.json": "df26c20bc2afc7e21f1d42b666ebf2eba2d03a67c425b8690a11236730716ad6",
    "docs/ai/schemas/v2/report-body.schema.json": "f5bf1489d8c56b3bb2c8e8445db82c0abafb5924e5f98929d855fdf08f17b51d",
    "docs/ai/schemas/v2/error-envelope.schema.json": "72fc5bd7742d6c78bfe64eae785954b51189ef5f35d5c70e297f9d943b394abc",
}
INPUT_LIMIT = 1_048_576
WIRE_LIMIT = 25_165_824
VERSION_KEYS = {
    "serviceVersion", "ruleSetVersion", "extractionSchemaVersion", "scorePolicyVersion",
    "modelProvider", "modelId", "modelVersion", "promptVersion",
}
CONTEXT_KEYS = {
    "vendorContext": ("accountHistorySnapshot", "relatedPartySnapshot"),
    "comparisonContext": ("quoteComparatorsSnapshot", "crossProjectDuplicateSnapshot", "procurementHistorySnapshot"),
}
REF_KEYS = ("evidenceId", "kind", "version", "contentSha256", "leafKeccak256")
ERROR_MATRIX = {
    "INPUT_FORMAT_INVALID": (400, False), "REQUEST_TOO_LARGE": (413, False),
    "AUTHENTICATION_REQUIRED": (401, False), "FORBIDDEN": (403, False),
    "RESOURCE_NOT_FOUND": (404, False), "OPERATION_NOT_FOUND": (404, False),
    "IDEMPOTENCY_CONFLICT": (409, False), "IDEMPOTENCY_KEY_EXPIRED": (409, False),
    "RATE_LIMITED": (429, True), "DOCUMENT_READ_UNAVAILABLE": (503, True),
    "DOCUMENT_HASH_MISMATCH": (422, False), "EVIDENCE_BINDING_MISMATCH": (422, False),
    "REGISTRY_SNAPSHOT_UNVERIFIED": (503, True), "REGISTRY_SNAPSHOT_STALE": (409, False),
    "EXTERNAL_DATA_UNAVAILABLE": (503, True), "DEPENDENCY_UNAVAILABLE": (503, True),
    "MODEL_TIMEOUT": (504, True), "MODEL_FAILURE": (502, True),
    "MODEL_OUTPUT_INVALID": (502, False), "CANONICALIZATION_FAILED": (422, False),
    "RULE_FAILURE": (500, False), "SCORING_POLICY_UNCONFIRMED": (409, False),
    "VERSION_UNSUPPORTED": (422, False), "REQUEST_DEADLINE_EXCEEDED": (504, False),
    "INTERNAL_ERROR": (500, False),
}


class CheckFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def check(condition, code):
    if not condition:
        raise CheckFailure(code)


def exact(value, names, code):
    check(type(value) is dict and set(value) == set(names), code)


def sha(raw):
    return "0x" + hashlib.sha256(raw).hexdigest()


def load_json(raw, limit=WIRE_LIMIT):
    check(len(raw) <= limit and not raw.startswith(b"\xef\xbb\xbf"), "JSON_ENCODING_OR_LIMIT")
    def pairs(items):
        value = {}
        for key, item in items:
            check(key not in value, "JSON_DUPLICATE_KEY")
            value[key] = item
        return value
    def integer(token):
        check(re.fullmatch(r"0|[1-9][0-9]*", token) is not None and len(token) <= 10
              and int(token) <= 2_147_483_647, "JSON_NUMBER_TOKEN")
        return int(token)
    def forbidden(_):
        raise CheckFailure("JSON_NUMBER_TOKEN")
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                           parse_int=integer, parse_float=forbidden, parse_constant=forbidden)
    except CheckFailure:
        raise
    except Exception:
        raise CheckFailure("JSON_PARSE_FAILED") from None
    def tree(item, depth=1):
        check(depth <= 32, "JSON_DEPTH")
        if type(item) is str:
            check(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "JSON_SURROGATE")
        elif type(item) is dict:
            check(len(item) <= 1024, "JSON_COLLECTION_LIMIT")
            for key, child in item.items():
                check(key.isascii(), "JSON_OBJECT_KEY")
                tree(child, depth + 1)
        elif type(item) is list:
            check(len(item) <= 1024, "JSON_COLLECTION_LIMIT")
            for child in item:
                tree(child, depth + 1)
    tree(value)
    return value


def decode64(value, limit):
    check(type(value) is str and len(value) <= 4 * ((limit + 2) // 3), "BASE64_LIMIT")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raise CheckFailure("BASE64_INVALID") from None
    check(len(raw) <= limit and base64.b64encode(raw).decode("ascii") == value, "BASE64_INVALID")
    return raw


def endpoint(value):
    check(type(value) is str and value.isascii(), "URL_INVALID")
    try:
        url = urllib.parse.urlsplit(value)
        check(url.scheme in ("http", "https") and url.hostname and url.username is None
              and url.password is None and url.path in ("", "/") and not url.query
              and not url.fragment and url.port is not None and 1 <= url.port <= 65535,
              "URL_INVALID")
        address = ipaddress.ip_address(url.hostname)
        approved = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
        check(address.version == 4 and any(address in n for n in approved), "LAN_REQUIRES_PRIVATE_IPV4")
        return value.rstrip("/")
    except CheckFailure:
        raise
    except Exception:
        raise CheckFailure("URL_INVALID") from None


class Frozen:
    def __init__(self, root):
        self.root = Path(root)
        for name, digest in FROZEN_FILES.items():
            check(hashlib.sha256((self.root / name).read_bytes()).hexdigest() == digest,
                  "FROZEN_DEPENDENCY_HASH_MISMATCH")
        spec = importlib.util.spec_from_file_location("mac_pog_frozen_cjson", self.root / "docs/ai/tools/cjson_reference.py")
        check(spec is not None and spec.loader is not None, "FROZEN_IMPORT_FAILED")
        self.cjson = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cjson)
        check(self.cjson.keccak256(b"") == "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
              and self.cjson.keccak256(b"abc") == "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
              "ETHEREUM_KECCAK_KAT_FAILED")
        try:
            from jsonschema import Draft202012Validator, FormatChecker
            from referencing import Registry, Resource
        except ImportError:
            raise CheckFailure("CONTROLLED_VENV_DEPENDENCY_MISSING") from None
        def no_remote(_):
            raise CheckFailure("REMOTE_SCHEMA_FORBIDDEN")
        registry = Registry(retrieve=no_remote)
        schemas = {}
        for name in ("common", "input", "report-body", "error-envelope"):
            schema = json.loads((self.root / f"docs/ai/schemas/v2/{name}.schema.json").read_bytes())
            Draft202012Validator.check_schema(schema)
            registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
            schemas[name] = schema
        self.schemas = schemas
        self.validators = {name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
                           for name, schema in schemas.items()}

    def schema(self, name, value):
        check(next(self.validators[name].iter_errors(value), None) is None, "SCHEMA_" + name.upper())

    def canonical(self, raw):
        value = self.cjson.load_raw(raw)
        check(self.cjson.canonical_bytes(value) == raw, "CANONICAL_BYTES_MISMATCH")
        return value

    def request(self, raw):
        request = load_json(raw)
        exact(request, ("schemaVersion", "operationId", "aiRequestId", "purpose", "attempt", "remainingMillis",
                        "payloadHash", "inputHash", "inputBytesBase64", "documents", "externalSnapshots"), "REQUEST_KEYS")
        check(request["schemaVersion"] == "pog.ai.byte-request/1" and request["purpose"] == "diagnostic", "REQUEST_VERSION_OR_PURPOSE")
        for key in ("operationId", "aiRequestId"):
            check(type(request[key]) is str and len(request[key]) <= 8192 and self.cjson.IDENTIFIER.fullmatch(request[key]), "REQUEST_ID")
        check(type(request["attempt"]) is int and request["attempt"] in (1, 2)
              and type(request["remainingMillis"]) is int and 1 <= request["remainingMillis"] <= 120_000, "REQUEST_BUDGET")
        input_raw = decode64(request["inputBytesBase64"], INPUT_LIMIT)
        value = self.canonical(input_raw)
        self.schema("input", value)
        def uint(item, bits):
            check(item is None or int(item) < 1 << bits, "INPUT_UINT_RANGE")
        for k in ("budgetAtomic", "unitPriceLimitAtomic"):
            uint(value["projectPolicy"][k], 256)
        uint(value["evidenceSnapshot"]["deployment"]["chainId"], 256)
        uint(value["evidenceSnapshot"]["evidenceVersion"], 64)
        uint(value["registrySnapshot"]["blockNumber"], 256)
        for doc in value["documents"] + value["evidenceSnapshot"]["documentVersions"]:
            uint(doc["version"], 64)
        for k in (("budgetCap",) if value["stage"] == 0 else ("reservedAmount", "invoiceAmount")):
            uint(value["registrySnapshot"][k], 256)
        stage_data = value["procurementData"] if value["stage"] == 0 else value["deliveryData"]
        for key, item in stage_data.items():
            if key.endswith("Version"):
                uint(item, 64)
            elif key in ("quantity", "quoteAmountAtomic", "poQuantity", "grnQuantity", "invoiceQuantity"):
                uint(item, 256)
        check(request["payloadHash"] == sha(input_raw) and request["inputHash"] == self.cjson.keccak256(input_raw), "INPUT_HASH_MISMATCH")
        check(value["projectPolicy"]["periodStart"] <= value["projectPolicy"]["periodEnd"], "POLICY_PERIOD")
        snapshot = value["evidenceSnapshot"]
        documents = snapshot["documentVersions"]
        refs = value["documents"]
        check(len(documents) <= 8 and len(snapshot["externalSnapshots"]) <= 8, "RESOURCE_LIMIT")
        for collection in (documents, refs):
            self.cjson.ordered([(d["evidenceId"], int(d["version"])) for d in collection], "input documents")
            check(len({d["evidenceId"] for d in collection}) == len(collection), "EVIDENCE_DUPLICATE")
        by_evidence = {d["evidenceId"]: d for d in documents}
        check(set(by_evidence) == {r["evidenceId"] for r in refs}, "INPUT_EVIDENCE_COVERAGE")
        for ref in refs:
            doc = by_evidence[ref["evidenceId"]]
            check(all(ref[k] == doc[k] for k in REF_KEYS) and ref["pages"] == doc["pages"], "INPUT_EVIDENCE_BINDING")
            if doc["contentType"] == "application/pdf":
                check(1 <= len(doc["pages"]) <= 2, "PDF_PAGE_LIMIT")
                self.cjson.ordered(doc["pages"], "pages")
            else:
                check(doc["pages"] is None, "IMAGE_PAGES")
        total = len(input_raw)
        for key, id_key, declarations, cap in (
            ("documents", "documentVersionId", documents, 10_485_760),
            ("externalSnapshots", "snapshotId", snapshot["externalSnapshots"], 2_097_152),
        ):
            entries = request[key]
            check(type(entries) is list and len(entries) <= 8, "RESOURCE_LIMIT")
            for entry in entries:
                exact(entry, (id_key, "bytesBase64"), "RESOURCE_KEYS")
            self.cjson.ordered([e[id_key] for e in entries], "wire resources")
            declared = {d[id_key]: d for d in declarations}
            check(len(declared) == len(declarations) and set(declared) == {e[id_key] for e in entries}, "RESOURCE_COVERAGE")
            for entry in entries:
                data = decode64(entry["bytesBase64"], cap)
                total += len(data)
                check(total <= 16_777_216, "RESOURCE_TOTAL_LIMIT")
                doc = declared[entry[id_key]]
                hash_key = "leafKeccak256" if key == "documents" else "contentKeccak256"
                check(sha(data) == doc["contentSha256"] and self.cjson.keccak256(data) == doc[hash_key], "RESOURCE_HASH_MISMATCH")
                if key == "documents":
                    check(len(data) == doc["sizeBytes"], "DOCUMENT_SIZE_MISMATCH")
        external = {x["snapshotId"] for x in snapshot["externalSnapshots"]}
        self.cjson.ordered([x["snapshotId"] for x in snapshot["externalSnapshots"]], "external snapshots")
        for group, keys in CONTEXT_KEYS.items():
            check(all(value[group][k] is None or value[group][k] in external for k in keys), "CONTEXT_BINDING")
        registry = value["registrySnapshot"]
        expected_roles = {"poHash", "requestHash", "goodsRequestHash"} if value["stage"] == 0 else {"poHash", "invoiceHash", "goodsHash", "receiptDigest"}
        commitments = snapshot["commitments"]
        self.cjson.ordered([c["evidenceRole"] for c in commitments], "commitments")
        check({c["evidenceRole"] for c in commitments} == expected_roles, "COMMITMENT_ROLE_COVERAGE")
        by_version = {d["documentVersionId"]: d for d in documents}
        role_categories = {role: category for category, role in self.schemas["common"]["x-pog-registry-category-role-map"].items()}
        role_kinds = {"poHash": {"PO"}, "requestHash": {"ProcurementRequest"}, "goodsRequestHash": {"ProcurementRequest"}, "invoiceHash": {"Invoice"}, "goodsHash": {"GRN", "Photo", "Inspection"}}
        for item in commitments:
            role = item["evidenceRole"]
            check(item["commitmentHash"] == registry[role], "COMMITMENT_BINDING")
            check(item["scheme"] != "registered_manifest", "REGISTERED_MANIFEST_UNSUPPORTED")
            if item["scheme"] == "raw_file_keccak256":
                check(len(item["documentVersionIds"]) == 1 and item["documentVersionIds"][0] in by_version, "COMMITMENT_DOCUMENT")
                doc = by_version[item["documentVersionIds"][0]]
                check(doc["leafKeccak256"] == item["commitmentHash"] and doc["category"] == role_categories[role]
                      and doc["kind"] in role_kinds[role], "COMMITMENT_DOCUMENT_BINDING")
                version_key = {"poHash": "poVersion", "requestHash": "requestVersion", "invoiceHash": "invoiceVersion"}.get(role)
                if role == "goodsHash":
                    version_key = {"GRN": "grnVersion", "Inspection": "inspectionVersion"}.get(doc["kind"])
                check(version_key is None or doc["version"] == stage_data[version_key], "COMMITMENT_VERSION_BINDING")
            else:
                check(value["stage"] == 1 and role == "receiptDigest" and value["deliveryData"]["receiptAccepted"] is True, "RECEIPT_BINDING")
                check(not any(d["kind"] == "Receipt" and d["leafKeccak256"] == item["commitmentHash"] for d in documents), "RECEIPT_IS_NOT_FILE_HASH")
        fields = ["projectId", "procurementId", "foundation", "recipient", "vendor", "asset"]
        fields += ["budgetCap", "poHash", "requestHash", "goodsRequestHash"] if value["stage"] == 0 else ["reservedAmount", "poHash", "invoiceHash", "invoiceAmount", "goodsHash", "receiptDigest"]
        tag = b"POG_V2_PRE_EVIDENCE" if value["stage"] == 0 else b"POG_V2_FINAL_EVIDENCE"
        words = [bytes.fromhex(self.cjson.keccak256(tag)[2:])]
        for name in fields:
            item = registry[name]
            words.append(bytes.fromhex(item[2:]).rjust(32, b"\x00") if item.startswith("0x") else int(item).to_bytes(32, "big"))
        check(self.cjson.keccak256(b"".join(words)) == registry["currentEvidenceHash"], "REGISTRY_EVIDENCE_HASH")
        return request, value, input_raw

    def report(self, response, request, value, versions):
        raw = decode64(response["reportBytesBase64"], INPUT_LIMIT)
        body = self.canonical(raw)
        self.schema("report-body", body)
        self.cjson.validate_report(body)
        check(response["reportHash"] == self.cjson.keccak256(raw), "REPORT_HASH_MISMATCH")
        registry = value["registrySnapshot"]
        expected = {"stage": value["stage"], "projectId": registry["projectId"], "procurementId": registry["procurementId"],
                    "evidenceVersion": value["evidenceSnapshot"]["evidenceVersion"], "evidenceHash": registry["currentEvidenceHash"], "inputHash": request["inputHash"]}
        check(all(body[k] == v for k, v in expected.items()), "REPORT_INPUT_BINDING")
        check(body["executionMode"] == "model_and_rules" and body["versions"] == versions, "REAL_MODEL_VERSION_BINDING")
        check(body["outcome"] == 1 and body["riskScoreBps"] is None and body["versions"]["scorePolicyVersion"] is None,
              "DIAGNOSTIC_REVIEW_NULL_REQUIRED")
        declared = {d["evidenceId"]: d for d in value["evidenceSnapshot"]["documentVersions"]}
        input_refs = {r["evidenceId"]: r for r in value["documents"]}
        check({r["evidenceId"] for r in body["evidenceRefs"]} == set(input_refs), "REPORT_EVIDENCE_COVERAGE")
        for ref in body["evidenceRefs"]:
            doc = declared[ref["evidenceId"]]
            check(all(ref[k] == doc[k] for k in REF_KEYS), "REPORT_EVIDENCE_BINDING")
            pages = input_refs[ref["evidenceId"]]["pages"]
            check(ref["pages"] is None if pages is None else type(ref["pages"]) is list and set(ref["pages"]) <= set(pages), "REPORT_PAGE_BINDING")
        missing = {m["inputKey"]: m for m in body["missingInputs"]}
        for group, names in CONTEXT_KEYS.items():
            for name in names:
                if value[group][name] is None:
                    key = f"{group}.{name}"
                    check(body["completeness"] == "incomplete" and key in missing
                          and value["stage"] in missing[key]["requiredForStages"], "MISSING_CONTEXT_NOT_REPORTED")
        return raw, body

    def response(self, status, response, request, value, versions):
        check(type(response) is dict, "RESPONSE_NOT_OBJECT")
        if response.get("schemaVersion") == "pog.ai.error/0.2-candidate":
            self.schema("error-envelope", response)
            check(response["stage"] is None and response["procurementId"] is None, "EARLY_ERROR_BINDING")
            check(ERROR_MATRIX[response["code"]] == (status, response["retryable"]), "ERROR_HTTP_MAPPING")
            return None, None, response["code"]
        exact(response, ("schemaVersion", "operationId", "aiRequestId", "payloadHash", "status", "reportBytesBase64", "reportHash", "error", "signingEnvelope"), "RESPONSE_KEYS")
        check(response["schemaVersion"] == "pog.ai.byte-response/1", "RESPONSE_VERSION")
        check(all(response[k] == request[k] for k in ("operationId", "aiRequestId", "payloadHash")), "RESPONSE_OPERATION_BINDING")
        check(response["signingEnvelope"] is None, "SIGNATURE_NOT_ALLOWED")
        if response["status"] == "error":
            check(response["reportBytesBase64"] is None and response["reportHash"] is None, "ERROR_CONTAINS_REPORT")
            error = response["error"]
            self.schema("error-envelope", error)
            check(error["stage"] == value["stage"] and error["procurementId"] == value["registrySnapshot"]["procurementId"], "ERROR_INPUT_BINDING")
            check(ERROR_MATRIX[error["code"]] == (status, error["retryable"]), "ERROR_HTTP_MAPPING")
            return None, None, error["code"]
        check(status == 200 and response["status"] == "completed" and response["error"] is None, "RESPONSE_STATUS")
        raw, body = self.report(response, request, value, versions)
        return raw, body, None


def times():
    current = dt.datetime.now(dt.timezone.utc)
    return {"utc": current.isoformat(timespec="seconds"), "local": current.astimezone().isoformat(timespec="seconds")}


def config(raw):
    value = load_json(raw, 65_536)
    exact(value, ("schemaVersion", "baseUrl", "tokenFile", "requestFile", "outputDirectory", "repoRoot",
                  "expectedServerCommit", "expectedVersions", "windowsEvidenceReference"), "CONFIG_KEYS")
    check(value["schemaVersion"] == "pog.ai.mac-verifier-config/1", "CONFIG_VERSION")
    value["baseUrl"] = endpoint(value["baseUrl"])
    check(type(value["expectedServerCommit"]) is str and re.fullmatch(r"[0-9a-f]{40}", value["expectedServerCommit"]), "SERVER_COMMIT_REQUIRED")
    for name in ("tokenFile", "requestFile", "outputDirectory", "repoRoot"):
        check(type(value[name]) is str and Path(value[name]).is_absolute(), "ABSOLUTE_CONFIG_PATH_REQUIRED")
    check(type(value["windowsEvidenceReference"]) is str and 1 <= len(value["windowsEvidenceReference"]) <= 8192, "WINDOWS_EVIDENCE_REFERENCE_REQUIRED")
    exact(value["expectedVersions"], VERSION_KEYS, "EXPECTED_VERSION_KEYS")
    check(value["expectedVersions"]["scorePolicyVersion"] is None, "SCORING_NOT_APPROVED")
    for name in VERSION_KEYS - {"scorePolicyVersion"}:
        check(type(value["expectedVersions"][name]) is str and len(value["expectedVersions"][name]) > 0, "EXPECTED_VERSION_VALUE")
    return value


def read_token(path):
    path = Path(path)
    check(not path.is_symlink(), "TOKEN_SYMLINK_FORBIDDEN")
    if os.name == "posix":
        check(path.stat().st_mode & 0o077 == 0, "TOKEN_FILE_NOT_PRIVATE")
    raw = path.read_bytes()
    check(len(raw) <= 4096, "TOKEN_FILE_LIMIT")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
    check(32 <= len(raw) <= 1024 and re.fullmatch(rb"[A-Za-z0-9._~+/-]+=*", raw), "TOKEN_FILE_FORMAT")
    return raw


def save(path, raw):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckFailure("HTTP_REDIRECT_FORBIDDEN")


def no_secret(raw, parsed, token):
    """Check exact bytes plus decoded JSON strings, including Unicode escapes."""
    check(token not in raw, "CREDENTIAL_ECHO_REJECTED")
    secret = token.decode("ascii")
    def walk(item):
        if type(item) is str:
            check(secret not in item, "CREDENTIAL_ECHO_REJECTED")
        elif type(item) is dict:
            for key, value in item.items():
                walk(key)
                walk(value)
        elif type(item) is list:
            for child in item:
                walk(child)
    walk(parsed)


def clean_response(raw, parsed, token):
    """Run before persisting any HTTP response, receipt content or BODY."""
    no_secret(raw, parsed, token)
    if type(parsed) is dict and parsed.get("reportBytesBase64") is not None:
        report_raw = decode64(parsed["reportBytesBase64"], INPUT_LIMIT)
        no_secret(report_raw, load_json(report_raw, INPUT_LIMIT), token)


def fetch(url, token, method, body=None, timeout=130):
    request = urllib.request.Request(url, data=body, method=method,
        headers={"Authorization": "Bearer " + token.decode("ascii"), "Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        result = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        result = error
    with result:
        raw = result.read(WIRE_LIMIT + 1)
        check(len(raw) <= WIRE_LIMIT, "HTTP_RESPONSE_LIMIT")
        media = result.headers.get_content_type()
        check(media == "application/json", "HTTP_RESPONSE_CONTENT_TYPE")
        parsed = load_json(raw)
        clean_response(raw, parsed, token)
        return result.code, raw, parsed


def execute(value, fetcher=fetch):
    frozen = Frozen(value["repoRoot"])
    request_raw = Path(value["requestFile"]).read_bytes()
    check(hashlib.sha256(request_raw).hexdigest() == APPROVED_MAC_REQUEST_SHA256,
          "APPROVED_SYNTHETIC_REQUEST_MISMATCH")
    request, input_value, input_raw = frozen.request(request_raw)
    token = read_token(value["tokenFile"])
    output = Path(value["outputDirectory"])
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    receipt = {
        "schemaVersion": "pog.ai.mac-acceptance-receipt/1", "executionOrigin": "caller_environment_unattested",
        "startedAt": times(), "finishedAt": None, "result": "FAIL", "failureCode": None,
        "operationId": request["operationId"], "aiRequestId": request["aiRequestId"], "attempt": request["attempt"],
        "baseUrl": value["baseUrl"], "frozenInterfaceCommit": FROZEN_COMMIT,
        "canonicalProfile": "PoG-CJSON-0.2", "frozenDependencySha256": FROZEN_FILES,
        "expectedServerCommit": value["expectedServerCommit"], "serverCommitVerifiedByHttp": False,
        "windowsEvidenceReference": value["windowsEvidenceReference"], "expectedVersions": value["expectedVersions"],
        "requestSha256": sha(request_raw), "payloadHash": sha(input_raw), "inputHash": frozen.cjson.keccak256(input_raw),
        "approvedSyntheticRequestSha256": APPROVED_MAC_REQUEST_SHA256,
        "health": None, "assessment": None, "savedReport": None,
        "generationStatus": "NOT_RUN", "businessAssessmentComplete": False,
        "signingEnabled": False, "macExecutionConfirmedBy": None, "apiStorageReceipt": None,
        "automaticRetryPerformed": False,
    }
    try:
        save(output / "input.cjson", input_raw)
        health_url = value["baseUrl"] + "/internal/v1/health"
        health_time = times()
        status, raw, health = fetcher(health_url, token, "GET", timeout=15)
        clean_response(raw, health, token)
        save(output / "health.response.json", raw)
        receipt["health"] = {"requestedAt": health_time, "receivedAt": times(), "url": health_url,
                             "httpStatus": status, "body": health, "responseSha256": sha(raw)}
        exact(health, ("schemaVersion", "status", "backendReady", "signingEnabled"), "HEALTH_KEYS")
        check(status == 200 and health["schemaVersion"] == "pog.ai.health/1" and health["status"] == "ok"
              and health["signingEnabled"] is False and type(health["backendReady"]) is bool, "HEALTH_CONTRACT")
        check(health["backendReady"] is True, "BACKEND_NOT_READY")
        assessment_url = value["baseUrl"] + "/internal/v1/assess-bytes"
        assessment_time = times()
        receipt["generationStatus"] = "REQUESTED"
        status, raw, response = fetcher(assessment_url, token, "POST", request_raw,
                                        timeout=min(130, request["remainingMillis"] / 1000 + 10))
        receipt["assessment"] = {"requestedAt": assessment_time, "receivedAt": times(), "url": assessment_url,
                                  "httpStatus": status, "responseFile": None, "responseArtifactSaved": False}
        clean_response(raw, response, token)
        report_raw, report, error = frozen.response(status, response, request, input_value, value["expectedVersions"])
        if error is not None:
            receipt["generationStatus"] = "error"
            receipt["assessment"]["error"] = {"code": error, "retryable": ERROR_MATRIX[error][1]}
            raise CheckFailure(error)
        no_secret(report_raw, report, token)
        save(output / "assessment.response.json", raw)
        receipt["assessment"].update(responseSha256=sha(raw), responseFile=str(output / "assessment.response.json"),
                                     responseArtifactSaved=True)
        save(output / "report.cjson", report_raw)
        check((output / "report.cjson").read_bytes() == report_raw, "SAVED_REPORT_BYTES_MISMATCH")
        receipt["savedReport"] = {"path": str(output / "report.cjson"), "byteLength": len(report_raw),
                                  "sha256": sha(report_raw), "reportHash": frozen.cjson.keccak256(report_raw),
                                  "savedBytesRechecked": True, "completeness": report["completeness"],
                                  "outcome": report["outcome"], "riskScoreBps": report["riskScoreBps"],
                                  "executionMode": report["executionMode"], "versions": report["versions"],
                                  "missingInputs": report["missingInputs"]}
        receipt["generationStatus"] = "completed"
        receipt["businessAssessmentComplete"] = report["completeness"] == "complete"
        receipt["result"] = "PASS_REPORT_GENERATED_SAVED"
    except CheckFailure as error:
        receipt["failureCode"] = error.code
        if receipt["generationStatus"] == "REQUESTED":
            receipt["generationStatus"] = "error"
    except KeyboardInterrupt:
        receipt["failureCode"] = "CLIENT_INTERRUPTED_OPERATION_STATUS_UNKNOWN"
    except Exception:
        receipt["failureCode"] = "CLIENT_FAILURE_OPERATION_STATUS_UNKNOWN"
    finally:
        receipt["finishedAt"] = times()
        save(output / "receipt.json", json_bytes(receipt))
    return receipt, 0 if receipt["result"] == "PASS_REPORT_GENERATED_SAVED" else 2


def main():
    # Fixed argument shape prevents argparse from echoing accidental secrets.
    if len(sys.argv) != 2:
        print("Usage: controlled-python -B verify_service.py CONFIG_FILE")
        return 2
    try:
        value = config(Path(sys.argv[1]).read_bytes())
        _, code = execute(value)
        print("PASS_REPORT_GENERATED_SAVED" if code == 0 else "FAIL: inspect private receipt.json")
        return code
    except CheckFailure as error:
        print("FAIL: " + error.code)
    except KeyboardInterrupt:
        print("FAIL: CLIENT_INTERRUPTED")
    except Exception:
        print("FAIL: CLIENT_SETUP_OR_SAVE_FAILED")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
