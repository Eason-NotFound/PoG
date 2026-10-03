#!/usr/bin/env node
// Offline candidate reference; no model, networking, keys, chain or signatures.
// Strict TypeScript token parser, canonical encoder and flat-lane Keccak.
import * as fs from 'node:fs';
import * as path from 'node:path';
import { TextDecoder } from 'node:util';

export type JsonValue = null | boolean | number | string | JsonValue[] | JsonObject;
export interface JsonObject { [key: string]: JsonValue }
type OrderScalar = string | number | bigint;
type OrderKey = OrderScalar | OrderScalar[];
type EncodeKind = 'report' | 'input' | 'generic';
interface PositiveVector {
  id: string; kind: EncodeKind; source: string; canonicalUtf8: string;
  canonicalHex: string; canonicalLength: number; expectedKeccak256: string;
  inputSource?: string;
}
interface NegativeVector { id: string; kind: EncodeKind; source: string; expectedError: string }
interface HashRelation { left: string; right: string; relation: 'equal' | 'different' }
interface Manifest {
  profile: string; positiveVectors: PositiveVector[];
  negativeVectors: NegativeVector[]; hashRelations: HashRelation[];
}

const PROFILE = 'PoG-CJSON-0.2';
const BODY_VERSION = 'pog.ai.report/0.2-candidate';
const MAX_CANONICAL = 1048576;
const HEX32 = /^0x[0-9a-f]{64}(?![\s\S])/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]*(?![\s\S])/;
const DECIMAL = /^(?:0|[1-9][0-9]*)(?![\s\S])/;

export class ValidationError extends Error {
  constructor(public readonly code: string, message: string) { super(message); }
}
function check(condition: unknown, code: string, message: string): asserts condition {
  if (!condition) throw new ValidationError(code, message);
}
function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function errorCode(error: unknown): string { return error instanceof ValidationError ? error.code : ''; }

export function loadRaw(raw: Buffer): JsonValue {
  check(raw.length <= 4 * MAX_CANONICAL, 'RAW_LIMIT', 'raw JSON exceeds reference limit');
  check(!(raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf), 'BOM', 'UTF-8 BOM forbidden');
  let source: string;
  try { source = new TextDecoder('utf-8', { fatal: true }).decode(raw); }
  catch (error) { throw new ValidationError('INVALID_UTF8', message(error)); }
  let position = 0;
  function white(): void { while (/[ \t\n\r]/.test(source[position] || '\uFFFF')) position++; }
  function stringToken(): string {
    const start = position++;
    while (position < source.length) {
      const char = source[position++];
      if (char === '\\') { position++; continue; }
      if (char === '"') {
        try {
          const decoded: unknown = JSON.parse(source.slice(start, position));
          check(typeof decoded === 'string', 'JSON_SYNTAX', 'string token required');
          return decoded;
        } catch (error) { throw new ValidationError('JSON_SYNTAX', message(error)); }
      }
    }
    throw new ValidationError('JSON_SYNTAX', 'unterminated JSON string');
  }
  function value(depth = 1): JsonValue {
    check(depth <= 32, 'DEPTH_LIMIT', 'maximum nesting depth is 32'); white();
    const char = source[position];
    if (char === '"') return stringToken();
    if (char === '{') {
      position++; white(); const result: JsonObject = Object.create(null) as JsonObject;
      if (source[position] === '}') { position++; return result; }
      while (true) {
        check(source[position] === '"', 'JSON_SYNTAX', 'object key string required');
        const key = stringToken();
        check(!Object.hasOwn(result, key), 'DUPLICATE_KEY', `decoded duplicate key: ${key}`);
        white(); check(source[position++] === ':', 'JSON_SYNTAX', 'colon required');
        result[key] = value(depth + 1); white(); const separator = source[position++];
        if (separator === '}') return result;
        check(separator === ',', 'JSON_SYNTAX', 'comma or object close required'); white();
      }
    }
    if (char === '[') {
      position++; white(); const result: JsonValue[] = [];
      if (source[position] === ']') { position++; return result; }
      while (true) {
        result.push(value(depth + 1)); white();
        check(result.length <= 1024, 'ARRAY_LIMIT', 'maximum array length is 1024');
        const separator = source[position++];
        if (separator === ']') return result;
        check(separator === ',', 'JSON_SYNTAX', 'comma or array close required'); white();
      }
    }
    const literals: [string, JsonValue][] = [['null', null], ['true', true], ['false', false]];
    for (const [literal, result] of literals) {
      if (source.startsWith(literal, position)) { position += literal.length; return result; }
    }
    if (/^(?:NaN|Infinity|-Infinity)/.test(source.slice(position))) {
      throw new ValidationError('NUMBER_TOKEN', 'non-JSON number forbidden');
    }
    const numeric = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(source.slice(position));
    check(numeric !== null, 'JSON_SYNTAX', 'JSON value required'); position += numeric[0].length;
    check(DECIMAL.test(numeric[0]), 'NUMBER_TOKEN', 'unsigned decimal integer tokens only');
    check(numeric[0].length <= 10 && BigInt(numeric[0]) <= 2147483647n,
      'INTEGER_RANGE', 'wide integers must use decimal strings');
    return Number(numeric[0]);
  }
  const result = value(); white();
  check(position === source.length, 'JSON_SYNTAX', 'unexpected trailing JSON content');
  validateTree(result); return result;
}

export function validateTree(value: JsonValue, depth = 1): void {
  check(depth <= 32, 'DEPTH_LIMIT', 'maximum nesting depth is 32');
  if (typeof value === 'string') {
    let length = 0;
    for (const char of value) {
      const code = char.codePointAt(0)!; length++;
      check(code < 0xd800 || code > 0xdfff, 'INVALID_UNICODE', 'isolated surrogate forbidden');
    }
    check(length <= 8192, 'STRING_LIMIT', 'maximum string length is 8192 scalars');
  } else if (typeof value === 'number') {
    check(Number.isInteger(value) && value >= 0 && value <= 2147483647 && !Object.is(value, -0),
      'INTEGER_RANGE', 'small unsigned integers only');
  } else if (value === null || typeof value === 'boolean') { /* JSON literals */
  } else if (Array.isArray(value)) {
    check(value.length <= 1024, 'ARRAY_LIMIT', 'maximum array length is 1024');
    value.forEach(item => validateTree(item, depth + 1));
  } else if (typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      check([...key].every(c => c.codePointAt(0)! <= 127), 'OBJECT_KEY', 'object keys must be ASCII');
      validateTree(key, depth + 1); validateTree(item, depth + 1);
    }
  } else throw new ValidationError('JSON_TYPE', 'only JSON values allowed');
}
function object(value: JsonValue, location: string): JsonObject {
  check(value !== null && typeof value === 'object' && !Array.isArray(value),
    'BODY_SCHEMA', `${location}: object required`); return value;
}
function exactKeys(value: JsonValue, expected: string[], location: string): JsonObject {
  const result = object(value, location);
  check(Object.keys(result).length === expected.length && expected.every(k => Object.hasOwn(result, k)),
    'BODY_SCHEMA', `${location}: exact keys required`); return result;
}
function string(value: JsonValue, location: string, identifier = false): string {
  check(typeof value === 'string' && (!identifier || ID.test(value)), 'BODY_SCHEMA',
    `${location}: ${identifier ? 'ASCII machine identifier' : 'string'} required`); return value;
}
function nullableString(value: JsonValue, location: string, identifier = false): string | null {
  return value === null ? null : string(value, location, identifier);
}
function uintString(value: JsonValue, bits: number, location: string): string {
  const result = string(value, location);
  check(DECIMAL.test(result), 'BODY_SCHEMA', `${location}: canonical decimal string required`);
  check(result.length <= ((1n << BigInt(bits)) - 1n).toString().length && BigInt(result) < (1n << BigInt(bits)),
    'WIDE_RANGE', `${location}: uint${bits} overflow`); return result;
}
function integer(value: JsonValue, min: number, max: number, location: string): number {
  check(typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max,
    'BODY_SCHEMA', `${location}: integer ${min}..${max} required; bool is not integer`); return value;
}
function nullableInteger(value: JsonValue, min: number, max: number, location: string): number | null {
  return value === null ? null : integer(value, min, max, location);
}
function array(value: JsonValue, location: string): JsonValue[] {
  check(Array.isArray(value), 'BODY_SCHEMA', `${location}: array required`); return value;
}
function compare(a: OrderKey, b: OrderKey): number {
  if (Array.isArray(a) && Array.isArray(b)) {
    check(a.length === b.length, 'BODY_SCHEMA', 'sort tuple shape mismatch');
    for (let i = 0; i < a.length; i++) { const c = compare(a[i], b[i]); if (c) return c; }
    return 0;
  }
  if (typeof a === 'string' && typeof b === 'string' || typeof a === 'number' && typeof b === 'number' ||
      typeof a === 'bigint' && typeof b === 'bigint') return a < b ? -1 : a > b ? 1 : 0;
  throw new ValidationError('BODY_SCHEMA', 'sort key types mismatch');
}
function ordered(values: OrderKey[], location: string): void {
  check(values.slice(1).every((item, i) => compare(values[i], item) < 0),
    'ARRAY_ORDER', `${location}: strictly increasing order, no duplicates`);
}

export function validateReport(value: JsonValue): void {
  const body = exactKeys(value, ['schemaVersion', 'stage', 'projectId', 'procurementId', 'evidenceVersion',
    'evidenceHash', 'inputHash', 'executionMode', 'versions', 'outcome', 'riskScoreBps',
    'completeness', 'summary', 'evidenceRefs', 'findings', 'missingInputs'], 'BODY');
  check(body.schemaVersion === BODY_VERSION, 'BODY_SCHEMA', 'wrong ReportBody schemaVersion');
  integer(body.stage, 0, 1, 'stage'); integer(body.outcome, 0, 2, 'outcome');
  nullableInteger(body.riskScoreBps, 0, 10000, 'riskScoreBps');
  for (const key of ['projectId', 'procurementId', 'evidenceHash', 'inputHash']) {
    check(HEX32.test(string(body[key], key)), 'BODY_SCHEMA', `${key}: lowercase bytes32 required`);
  }
  uintString(body.evidenceVersion, 64, 'evidenceVersion');
  const executionMode = string(body.executionMode, 'executionMode');
  check(['synthetic_fixture', 'rules_only', 'model_and_rules'].includes(executionMode), 'BODY_SCHEMA', 'invalid executionMode');
  check(['complete', 'incomplete'].includes(string(body.completeness, 'completeness')), 'BODY_SCHEMA', 'invalid completeness');
  string(body.summary, 'summary');
  const vk = ['serviceVersion', 'ruleSetVersion', 'extractionSchemaVersion', 'scorePolicyVersion', 'modelProvider', 'modelId', 'modelVersion', 'promptVersion'];
  const versions = exactKeys(body.versions, vk, 'versions');
  for (const key of vk) check(versions[key] === null || typeof versions[key] === 'string' && versions[key].length > 0,
    'BODY_SCHEMA', `versions.${key}: nonempty string or null`);
  for (const key of vk.slice(0, 3)) check(versions[key] !== null, 'BODY_SCHEMA', `versions.${key} cannot be null`);
  check(versions.scorePolicyVersion !== null || body.riskScoreBps === null,
    'BODY_SCHEMA', 'unknown scoring policy requires null risk score');
  check(vk.slice(4).every(key => executionMode === 'model_and_rules' ? versions[key] !== null : versions[key] === null),
    'BODY_SCHEMA', 'model/prompt version shape does not match executionMode');
  const refs = array(body.evidenceRefs, 'evidenceRefs'); const byId = new Map<string, JsonObject>();
  const refSortKeys: OrderKey[] = [];
  for (const rawRef of refs) {
    const ref = exactKeys(rawRef, ['evidenceId', 'kind', 'version', 'contentSha256', 'leafKeccak256', 'pages'], 'EvidenceRef');
    const evidenceId = string(ref.evidenceId, 'evidenceId', true);
    check(!byId.has(evidenceId), 'BODY_SCHEMA', 'evidenceId must be unique'); byId.set(evidenceId, ref);
    const kind = string(ref.kind, 'kind');
    check(['ProcurementRequest', 'Quote', 'PO', 'GRN', 'Invoice', 'Receipt', 'Photo', 'Inspection'].includes(kind),
      'BODY_SCHEMA', 'unknown EvidenceRef.kind');
    const version = uintString(ref.version, 64, 'EvidenceRef.version'); refSortKeys.push([evidenceId, BigInt(version)]);
    for (const key of ['contentSha256', 'leafKeccak256']) check(HEX32.test(string(ref[key], key)),
      'BODY_SCHEMA', 'EvidenceRef hash must be lowercase bytes32');
    // Photo is always non-paged. Other business kinds can also be images; their
    // MIME/page consistency requires the trusted input snapshot, not kind guesses.
    check(kind !== 'Photo' || ref.pages === null, 'BODY_SCHEMA', 'Photo requires pages=null');
    if (ref.pages !== null) {
      const pages = array(ref.pages, 'pages').map(p => integer(p, 1, 2147483647, 'page'));
      ordered(pages, 'pages');
    }
  }
  ordered(refSortKeys, 'evidenceRefs');
  const findings = array(body.findings, 'findings'); const seen = new Set<string>();
  const findingSortKeys: OrderKey[] = [];
  for (const rawFinding of findings) {
    const finding = exactKeys(rawFinding, ['findingId', 'reasonCode', 'severity', 'message', 'evidenceLocators'], 'Finding');
    const findingId = string(finding.findingId, 'findingId', true);
    const reasonCode = string(finding.reasonCode, 'reasonCode', true);
    findingSortKeys.push([reasonCode, findingId]);
    check(!seen.has(findingId), 'BODY_SCHEMA', 'findingId must be unique'); seen.add(findingId);
    check(['information', 'review'].includes(string(finding.severity, 'severity')), 'BODY_SCHEMA', 'invalid severity');
    string(finding.message, 'message'); const sortKeys: OrderKey[] = [];
    for (const rawLoc of array(finding.evidenceLocators, 'evidenceLocators')) {
      const loc = exactKeys(rawLoc, ['evidenceId', 'page', 'field'], 'Locator');
      const evidenceId = string(loc.evidenceId, 'Locator.evidenceId', true);
      const ref = byId.get(evidenceId); check(ref !== undefined, 'BODY_SCHEMA', 'locator references unknown evidence');
      const page = nullableInteger(loc.page, 1, 2147483647, 'Locator.page');
      if (page !== null) check(ref.pages !== null && array(ref.pages, 'pages').includes(page),
        'BODY_SCHEMA', 'locator page not in reference pages');
      const field = nullableString(loc.field, 'Locator.field', true);
      sortKeys.push([evidenceId, page === null ? -1 : page, field === null ? '' : field]);
    }
    ordered(sortKeys, 'evidenceLocators');
  }
  ordered(findingSortKeys, 'findings');
  const missing = array(body.missingInputs, 'missingInputs'); const missingKeys: OrderKey[] = [];
  for (const rawItem of missing) {
    const item = exactKeys(rawItem, ['inputKey', 'reasonCode', 'requiredForStages'], 'MissingInput');
    missingKeys.push(string(item.inputKey, 'inputKey', true)); string(item.reasonCode, 'reasonCode', true);
    const stages = array(item.requiredForStages, 'requiredForStages').map(s => integer(s, 0, 1, 'requiredForStages'));
    check(stages.length > 0, 'BODY_SCHEMA', 'requiredForStages must be nonempty'); ordered(stages, 'requiredForStages');
  }
  ordered(missingKeys, 'missingInputs');
  if (body.completeness === 'incomplete') check(body.outcome === 1 && body.riskScoreBps === null && missing.length > 0,
    'BODY_SCHEMA', 'incomplete requires Review/null and explicit missingInputs');
  else check(missing.length === 0, 'BODY_SCHEMA', 'complete requires missingInputs=[]');
  if (body.riskScoreBps === null) check(body.outcome === 1, 'BODY_SCHEMA', 'unknown score cannot imply Pass or Reject');
}

function escapeString(value: string): string {
  let output = '"';
  for (const char of value) {
    const cp = char.codePointAt(0)!;
    if (cp === 34) output += '\\"';
    else if (cp === 92) output += '\\\\';
    else if (cp < 32) output += '\\u' + cp.toString(16).padStart(4, '0');
    else output += char;
  }
  return output + '"';
}
export function canonicalBytes(value: JsonValue): Buffer {
  validateTree(value);
  function emit(item: JsonValue): string {
    if (item === null) return 'null';
    if (typeof item === 'boolean') return item ? 'true' : 'false';
    if (typeof item === 'number') return String(item);
    if (typeof item === 'string') return escapeString(item);
    if (Array.isArray(item)) return '[' + item.map(emit).join(',') + ']';
    return '{' + Object.keys(item).sort().map(k => escapeString(k) + ':' + emit(item[k])).join(',') + '}';
  }
  const result = Buffer.from(emit(value), 'utf8');
  check(result.length <= MAX_CANONICAL, 'BODY_LIMIT', 'canonical object exceeds 1 MiB'); return result;
}

// Keccak-f1600 with flat lanes and in-place rho/pi. Round constants generated
// by the Keccak LFSR; Ethereum suffix 0x01, never NIST SHA3 suffix 0x06.
const MASK64 = (1n << 64n) - 1n;
const RHO = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44];
const PI = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1];
function rot(a: bigint, shift: number): bigint {
  const n = BigInt(shift); return ((a << n) | (a >> (64n - n))) & MASK64;
}
export function keccak256(raw: Buffer): string {
  const rate = 136; const padded = Buffer.alloc(Math.ceil((raw.length + 1) / rate) * rate);
  raw.copy(padded); padded[raw.length] = 1; padded[padded.length - 1] |= 128;
  const state: bigint[] = Array<bigint>(25).fill(0n);
  for (let offset = 0; offset < padded.length; offset += rate) {
    for (let lane = 0; lane < rate / 8; lane++) state[lane] ^= padded.readBigUInt64LE(offset + lane * 8);
    let lfsr = 1;
    for (let round = 0; round < 24; round++) {
      const c: bigint[] = Array<bigint>(5).fill(0n);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) c[x] ^= state[x + 5 * y];
      for (let x = 0; x < 5; x++) {
        const d = c[(x + 4) % 5] ^ rot(c[(x + 1) % 5], 1);
        for (let y = 0; y < 5; y++) state[x + 5 * y] ^= d;
      }
      let carry = state[1];
      for (let step = 0; step < 24; step++) {
        const t = state[PI[step]]; state[PI[step]] = rot(carry, RHO[step]); carry = t;
      }
      for (let y = 0; y < 5; y++) {
        const row = state.slice(y * 5, y * 5 + 5);
        for (let x = 0; x < 5; x++) state[y * 5 + x] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5]);
      }
      let rc = 0n;
      for (let j = 0; j < 7; j++) {
        if (lfsr & 1) rc ^= 1n << BigInt((1 << j) - 1);
        lfsr = ((lfsr << 1) ^ (lfsr & 0x80 ? 0x71 : 0)) & 255;
      }
      state[0] ^= rc;
    }
  }
  const output = Buffer.alloc(32);
  for (let lane = 0; lane < 4; lane++) output.writeBigUInt64LE(state[lane], lane * 8);
  return '0x' + output.toString('hex');
}
export function encodeFile(file: string, kind: EncodeKind): { object: JsonValue; bytes: Buffer; hash: string } {
  const value = loadRaw(fs.readFileSync(file)); if (kind === 'report') validateReport(value);
  const bytes = canonicalBytes(value); return { object: value, bytes, hash: keccak256(bytes) };
}
function kind(value: JsonValue): EncodeKind {
  check(value === 'report' || value === 'input' || value === 'generic', 'MANIFEST', 'unknown kind'); return value;
}
function parseManifest(value: JsonValue): Manifest {
  const raw = object(value, 'manifest');
  return {
    profile: string(raw.profile, 'profile'),
    positiveVectors: array(raw.positiveVectors, 'positiveVectors').map(value => {
      const item = object(value, 'positiveVector');
      const result: PositiveVector = {
        id: string(item.id, 'id'), kind: kind(item.kind), source: string(item.source, 'source'),
        canonicalUtf8: string(item.canonicalUtf8, 'canonicalUtf8'), canonicalHex: string(item.canonicalHex, 'canonicalHex'),
        canonicalLength: integer(item.canonicalLength, 0, MAX_CANONICAL, 'canonicalLength'),
        expectedKeccak256: string(item.expectedKeccak256, 'expectedKeccak256'),
      };
      if (Object.hasOwn(item, 'inputSource')) result.inputSource = string(item.inputSource, 'inputSource');
      return result;
    }),
    negativeVectors: array(raw.negativeVectors, 'negativeVectors').map(value => {
      const item = object(value, 'negativeVector');
      return { id: string(item.id, 'id'), kind: kind(item.kind), source: string(item.source, 'source'),
        expectedError: string(item.expectedError, 'expectedError') };
    }),
    hashRelations: array(raw.hashRelations, 'hashRelations').map(value => {
      const item = object(value, 'hashRelation'); const relation = item.relation;
      check(relation === 'equal' || relation === 'different', 'MANIFEST', 'unknown hash relation');
      return { left: string(item.left, 'left'), right: string(item.right, 'right'), relation };
    }),
  };
}
function verifyManifest(file: string): number {
  const manifest = parseManifest(loadRaw(fs.readFileSync(file))); const root = path.dirname(file);
  check(manifest.profile === PROFILE, 'MANIFEST', 'wrong profile'); let pass = 0, fail = 0;
  for (const item of manifest.positiveVectors) {
    try {
      const { object: value, bytes, hash } = encodeFile(path.join(root, item.source), item.kind);
      check(bytes.equals(fs.readFileSync(path.join(root, item.canonicalUtf8))), 'BYTES_MISMATCH', item.id);
      check(bytes.toString('hex') === fs.readFileSync(path.join(root, item.canonicalHex), 'ascii').trim(), 'HEX_MISMATCH', item.id);
      check(bytes.length === item.canonicalLength && hash === item.expectedKeccak256, 'HASH_MISMATCH', item.id);
      if (item.inputSource) check(object(value, item.id).inputHash === encodeFile(path.join(root, item.inputSource), 'input').hash,
        'INPUT_BINDING', item.id);
      pass++;
    } catch (error) { fail++; console.error(`FAIL ${item.id}: ${errorCode(error)} ${message(error)}`); }
  }
  for (const item of manifest.negativeVectors) {
    try { encodeFile(path.join(root, item.source), item.kind); throw new Error('invalid source accepted'); }
    catch (error) {
      if (errorCode(error) === item.expectedError) pass++;
      else { fail++; console.error(`FAIL ${item.id}: expected ${item.expectedError}, got ${errorCode(error) || message(error)}`); }
    }
  }
  const hashes = new Map(manifest.positiveVectors.map(v => [v.id, v.expectedKeccak256]));
  for (const rel of manifest.hashRelations) {
    check(hashes.has(rel.left) && hashes.has(rel.right), 'MANIFEST', 'unknown hash relation vector');
    if ((hashes.get(rel.left) === hashes.get(rel.right)) === (rel.relation === 'equal')) pass++; else fail++;
  }
  console.log(JSON.stringify({ implementation: 'typescript', profile: PROFILE, pass, fail, skip: 0, independentAcceptance: 'NOT_RUN' }));
  return fail ? 1 : 0;
}
function selftest(): void {
  const vectors: [string, string][] = [
    ['', '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'],
    ['abc', '0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45'],
  ];
  for (const [source, hash] of vectors) check(keccak256(Buffer.from(source)) === hash, 'KAT_FAILURE', 'Ethereum Keccak KAT mismatch');
  console.log(JSON.stringify({ implementation: 'typescript', keccakKnownAnswerTests: 2, pass: 2, fail: 0 }));
}
function main(args: string[]): number {
  try {
    if (args[0] === 'selftest') selftest();
    else if (args[0] === 'verify' && args.length === 2) return verifyManifest(args[1]);
    else if (args[0] === 'encode' && args[1]) {
      let encodeKind: EncodeKind = 'report'; let output: string | null = null;
      for (let i = 2; i < args.length; i += 2) {
        check(['--kind', '--out'].includes(args[i]) && args[i + 1], 'USAGE', 'encode source [--kind report|input|generic] [--out path]');
        if (args[i] === '--kind') encodeKind = kind(args[i + 1]); else output = args[i + 1];
      }
      const { bytes, hash } = encodeFile(args[1], encodeKind); if (output) fs.writeFileSync(output, bytes);
      console.log(JSON.stringify({ profile: PROFILE, kind: encodeKind, canonicalLength: bytes.length, keccak256: hash,
        semanticValidation: encodeKind === 'report' ? 'ReportBody' : 'separate input schema required' }));
    } else throw new ValidationError('USAGE', 'selftest | verify manifest | encode source [--kind report|input|generic] [--out path]');
    return 0;
  } catch (error) { console.error(JSON.stringify({ error: errorCode(error) || 'FILE_ERROR', message: message(error) })); return 1; }
}
if (require.main === module) process.exitCode = main(process.argv.slice(2));
