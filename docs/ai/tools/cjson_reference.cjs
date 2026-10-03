#!/usr/bin/env node
'use strict';
// Legacy JavaScript reference; TypeScript acceptance uses cjson_reference.ts.
// Offline candidate reference only; no network/model/keys/chain/signatures.
// Independent token parser, encoder and flat-lane Keccak permutation.
const fs = require('node:fs');
const path = require('node:path');
const { TextDecoder } = require('node:util');
const PROFILE = 'PoG-CJSON-0.2';
const BODY_VERSION = 'pog.ai.report/0.2-candidate';
const MAX_CANONICAL = 1048576;
const HEX32 = /^0x[0-9a-f]{64}(?![\s\S])/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]*(?![\s\S])/;
const DECIMAL = /^(?:0|[1-9][0-9]*)(?![\s\S])/;
class ValidationError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
function check(condition, code, message) {
  if (!condition) throw new ValidationError(code, message);
}

function loadRaw(raw) {
  check(raw.length <= 4 * MAX_CANONICAL, 'RAW_LIMIT', 'raw JSON exceeds reference limit');
  check(!(raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf), 'BOM', 'UTF-8 BOM forbidden');
  let source;
  try { source = new TextDecoder('utf-8', { fatal: true }).decode(raw); }
  catch (error) { throw new ValidationError('INVALID_UTF8', error.message); }
  let position = 0;
  function white() { while (/[ \t\n\r]/.test(source[position] || '\uFFFF')) position++; }
  function stringToken() {
    const start = position++;
    while (position < source.length) {
      const char = source[position++];
      if (char === '\\') { position++; continue; }
      if (char === '"') {
        try { return JSON.parse(source.slice(start, position)); }
        catch (error) { throw new ValidationError('JSON_SYNTAX', error.message); }
      }
    }
    throw new ValidationError('JSON_SYNTAX', 'unterminated JSON string');
  }
  function value(depth = 1) {
    check(depth <= 32, 'DEPTH_LIMIT', 'maximum nesting depth is 32');
    white();
    const char = source[position];
    if (char === '"') return stringToken();
    if (char === '{') {
      position++; white();
      const result = Object.create(null);
      if (source[position] === '}') { position++; return result; }
      while (true) {
        check(source[position] === '"', 'JSON_SYNTAX', 'object key string required');
        const key = stringToken();
        check(!Object.hasOwn(result, key), 'DUPLICATE_KEY', `decoded duplicate key: ${key}`);
        white(); check(source[position++] === ':', 'JSON_SYNTAX', 'colon required');
        result[key] = value(depth + 1); white();
        const separator = source[position++];
        if (separator === '}') return result;
        check(separator === ',', 'JSON_SYNTAX', 'comma or object close required'); white();
      }
    }
    if (char === '[') {
      position++; white(); const result = [];
      if (source[position] === ']') { position++; return result; }
      while (true) {
        result.push(value(depth + 1)); white();
        check(result.length <= 1024, 'ARRAY_LIMIT', 'maximum array length is 1024');
        const separator = source[position++];
        if (separator === ']') return result;
        check(separator === ',', 'JSON_SYNTAX', 'comma or array close required'); white();
      }
    }
    for (const [literal, result] of [['null', null], ['true', true], ['false', false]]) {
      if (source.startsWith(literal, position)) { position += literal.length; return result; }
    }
    const invalidSpecial = /^(?:NaN|Infinity|-Infinity)/.exec(source.slice(position));
    if (invalidSpecial) throw new ValidationError('NUMBER_TOKEN', 'non-JSON number forbidden');
    const numeric = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(source.slice(position));
    check(numeric !== null, 'JSON_SYNTAX', 'JSON value required');
    position += numeric[0].length;
    check(DECIMAL.test(numeric[0]), 'NUMBER_TOKEN', 'unsigned decimal integer tokens only');
    check(numeric[0].length <= 10 && BigInt(numeric[0]) <= 2147483647n,
      'INTEGER_RANGE', 'wide integers must use decimal strings');
    return Number(numeric[0]);
  }
  const result = value(); white();
  check(position === source.length, 'JSON_SYNTAX', 'unexpected trailing JSON content');
  validateTree(result);
  return result;
}

function validateTree(value, depth = 1) {
  check(depth <= 32, 'DEPTH_LIMIT', 'maximum nesting depth is 32');
  if (typeof value === 'string') {
    let length = 0;
    for (const char of value) {
      const code = char.codePointAt(0); length++;
      check(code < 0xd800 || code > 0xdfff, 'INVALID_UNICODE', 'isolated surrogate forbidden');
    }
    check(length <= 8192, 'STRING_LIMIT', 'maximum string length is 8192 scalars');
  } else if (typeof value === 'number') {
    check(Number.isInteger(value) && value >= 0 && value <= 2147483647 && !Object.is(value, -0),
      'INTEGER_RANGE', 'small unsigned integers only');
  } else if (value === null || typeof value === 'boolean') { /* literals */
  } else if (Array.isArray(value)) {
    check(value.length <= 1024, 'ARRAY_LIMIT', 'maximum array length is 1024');
    value.forEach(item => validateTree(item, depth + 1));
  } else if (typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      check([...key].every(c => c.codePointAt(0) <= 127), 'OBJECT_KEY', 'object keys must be ASCII');
      validateTree(key, depth + 1); validateTree(item, depth + 1);
    }
  } else throw new ValidationError('JSON_TYPE', 'only JSON values allowed');
}
function exactKeys(value, expected, location) {
  check(value !== null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === expected.length && expected.every(k => Object.hasOwn(value, k)),
    'BODY_SCHEMA', `${location}: exact keys required`);
}
function string(value, location, identifier = false, nullable = false) {
  if (nullable && value === null) return;
  check(typeof value === 'string' && (!identifier || ID.test(value)), 'BODY_SCHEMA', `${location}: ${identifier ? 'ASCII machine identifier' : 'string'} required`);
}
function uintString(value, bits, location) {
  check(typeof value === 'string' && DECIMAL.test(value), 'BODY_SCHEMA', `${location}: canonical decimal string required`);
  check(value.length <= ((1n << BigInt(bits)) - 1n).toString().length && BigInt(value) < (1n << BigInt(bits)),
    'WIDE_RANGE', `${location}: uint${bits} overflow`);
}
function integer(value, min, max, location, nullable = false) {
  if (nullable && value === null) return;
  check(typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max,
    'BODY_SCHEMA', `${location}: integer ${min}..${max} required; bool is not integer`);
}
function array(value, location) { check(Array.isArray(value), 'BODY_SCHEMA', `${location}: array required`); }
function compare(a, b) {
  if (Array.isArray(a)) {
    for (let i = 0; i < a.length; i++) { const c = compare(a[i], b[i]); if (c) return c; }
    return 0;
  }
  return a < b ? -1 : a > b ? 1 : 0;
}
function ordered(values, location) {
  check(values.slice(1).every((item, i) => compare(values[i], item) < 0),
    'ARRAY_ORDER', `${location}: strictly increasing order, no duplicates`);
}

function validateReport(body) {
  exactKeys(body, ['schemaVersion', 'stage', 'projectId', 'procurementId', 'evidenceVersion',
    'evidenceHash', 'inputHash', 'executionMode', 'versions', 'outcome', 'riskScoreBps',
    'completeness', 'summary', 'evidenceRefs', 'findings', 'missingInputs'], 'BODY');
  check(body.schemaVersion === BODY_VERSION, 'BODY_SCHEMA', 'wrong ReportBody schemaVersion');
  integer(body.stage, 0, 1, 'stage'); integer(body.outcome, 0, 2, 'outcome');
  integer(body.riskScoreBps, 0, 10000, 'riskScoreBps', true);
  for (const key of ['projectId', 'procurementId', 'evidenceHash', 'inputHash']) {
    check(typeof body[key] === 'string' && HEX32.test(body[key]), 'BODY_SCHEMA', `${key}: lowercase bytes32 required`);
  }
  uintString(body.evidenceVersion, 64, 'evidenceVersion');
  check(['synthetic_fixture', 'rules_only', 'model_and_rules'].includes(body.executionMode), 'BODY_SCHEMA', 'invalid executionMode');
  check(['complete', 'incomplete'].includes(body.completeness), 'BODY_SCHEMA', 'invalid completeness');
  string(body.summary, 'summary');
  const versions = body.versions;
  const vk = ['serviceVersion', 'ruleSetVersion', 'extractionSchemaVersion', 'scorePolicyVersion', 'modelProvider', 'modelId', 'modelVersion', 'promptVersion'];
  exactKeys(versions, vk, 'versions');
  for (const key of vk) check(versions[key] === null || typeof versions[key] === 'string' && versions[key].length > 0,
    'BODY_SCHEMA', `versions.${key}: nonempty string or null`);
  for (const key of vk.slice(0, 3)) check(versions[key] !== null, 'BODY_SCHEMA', `versions.${key} cannot be null`);
  check(versions.scorePolicyVersion !== null || body.riskScoreBps === null,
    'BODY_SCHEMA', 'unknown scoring policy requires null risk score');
  check(vk.slice(4).every(key => body.executionMode === 'model_and_rules' ? versions[key] !== null : versions[key] === null),
    'BODY_SCHEMA', 'model/prompt version shape does not match executionMode');
  array(body.evidenceRefs, 'evidenceRefs');
  const byId = new Map();
  for (const ref of body.evidenceRefs) {
    exactKeys(ref, ['evidenceId', 'kind', 'version', 'contentSha256', 'leafKeccak256', 'pages'], 'EvidenceRef');
    string(ref.evidenceId, 'evidenceId', true);
    check(!byId.has(ref.evidenceId), 'BODY_SCHEMA', 'evidenceId must be unique'); byId.set(ref.evidenceId, ref);
    check(['ProcurementRequest', 'Quote', 'PO', 'GRN', 'Invoice', 'Receipt', 'Photo', 'Inspection'].includes(ref.kind), 'BODY_SCHEMA', 'unknown EvidenceRef.kind');
    uintString(ref.version, 64, 'EvidenceRef.version');
    for (const key of ['contentSha256', 'leafKeccak256']) check(typeof ref[key] === 'string' && HEX32.test(ref[key]), 'BODY_SCHEMA', 'EvidenceRef hash must be lowercase bytes32');
    check(ref.kind !== 'Photo' || ref.pages === null, 'BODY_SCHEMA', 'Photo requires pages=null');
    if (ref.pages !== null) {
      array(ref.pages, 'pages'); ref.pages.forEach(p => integer(p, 1, 2147483647, 'page')); ordered(ref.pages, 'pages');
    }
  }
  ordered(body.evidenceRefs.map(r => [r.evidenceId, BigInt(r.version)]), 'evidenceRefs');
  array(body.findings, 'findings'); const seen = new Set();
  for (const finding of body.findings) {
    exactKeys(finding, ['findingId', 'reasonCode', 'severity', 'message', 'evidenceLocators'], 'Finding');
    string(finding.findingId, 'findingId', true); string(finding.reasonCode, 'reasonCode', true);
    check(!seen.has(finding.findingId), 'BODY_SCHEMA', 'findingId must be unique'); seen.add(finding.findingId);
    check(['information', 'review'].includes(finding.severity), 'BODY_SCHEMA', 'invalid severity');
    string(finding.message, 'message'); array(finding.evidenceLocators, 'evidenceLocators'); const sortKeys = [];
    for (const loc of finding.evidenceLocators) {
      exactKeys(loc, ['evidenceId', 'page', 'field'], 'Locator'); string(loc.evidenceId, 'Locator.evidenceId', true);
      check(byId.has(loc.evidenceId), 'BODY_SCHEMA', 'locator references unknown evidence');
      integer(loc.page, 1, 2147483647, 'Locator.page', true);
      if (loc.page !== null) check(byId.get(loc.evidenceId).pages !== null && byId.get(loc.evidenceId).pages.includes(loc.page), 'BODY_SCHEMA', 'locator page not in reference pages');
      string(loc.field, 'Locator.field', true, true);
      sortKeys.push([loc.evidenceId, loc.page === null ? -1 : loc.page, loc.field === null ? '' : loc.field]);
    }
    ordered(sortKeys, 'evidenceLocators');
  }
  ordered(body.findings.map(f => [f.reasonCode, f.findingId]), 'findings');
  array(body.missingInputs, 'missingInputs');
  for (const item of body.missingInputs) {
    exactKeys(item, ['inputKey', 'reasonCode', 'requiredForStages'], 'MissingInput');
    string(item.inputKey, 'inputKey', true); string(item.reasonCode, 'reasonCode', true);
    array(item.requiredForStages, 'requiredForStages');
    check(item.requiredForStages.length > 0, 'BODY_SCHEMA', 'requiredForStages must be nonempty');
    item.requiredForStages.forEach(s => integer(s, 0, 1, 'requiredForStages'));
    ordered(item.requiredForStages, 'requiredForStages');
  }
  ordered(body.missingInputs.map(m => m.inputKey), 'missingInputs');
  if (body.completeness === 'incomplete') {
    check(body.outcome === 1 && body.riskScoreBps === null && body.missingInputs.length > 0,
      'BODY_SCHEMA', 'incomplete requires Review/null and explicit missingInputs');
  } else check(body.missingInputs.length === 0, 'BODY_SCHEMA', 'complete requires missingInputs=[]');
  if (body.riskScoreBps === null) check(body.outcome === 1, 'BODY_SCHEMA', 'unknown score cannot imply Pass or Reject');
}

function escapeString(value) {
  let output = '"';
  for (const char of value) {
    const cp = char.codePointAt(0);
    if (cp === 34) output += '\\"';
    else if (cp === 92) output += '\\\\';
    else if (cp < 32) output += '\\u' + cp.toString(16).padStart(4, '0');
    else output += char;
  }
  return output + '"';
}
function canonicalBytes(value) {
  validateTree(value);
  function emit(item) {
    if (item === null) return 'null';
    if (typeof item === 'boolean') return item ? 'true' : 'false';
    if (typeof item === 'number') return String(item);
    if (typeof item === 'string') return escapeString(item);
    if (Array.isArray(item)) return '[' + item.map(emit).join(',') + ']';
    return '{' + Object.keys(item).sort().map(k => escapeString(k) + ':' + emit(item[k])).join(',') + '}';
  }
  const result = Buffer.from(emit(value), 'utf8');
  check(result.length <= MAX_CANONICAL, 'BODY_LIMIT', 'canonical object exceeds 1 MiB');
  return result;
}

// Keccak-f1600 using flat lanes and the in-place rho/pi cycle. RC constants
// generated with the Keccak LFSR; not imported from the Python implementation.
const MASK64 = (1n << 64n) - 1n;
const RHO = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44];
const PI = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1];
function rot(a, n) { n = BigInt(n); return ((a << n) | (a >> (64n - n))) & MASK64; }
function keccak256(raw) {
  const rate = 136;
  const padded = Buffer.alloc(Math.ceil((raw.length + 1) / rate) * rate);
  raw.copy(padded); padded[raw.length] = 1; padded[padded.length - 1] |= 128;
  const state = Array(25).fill(0n);
  for (let offset = 0; offset < padded.length; offset += rate) {
    for (let lane = 0; lane < rate / 8; lane++) state[lane] ^= padded.readBigUInt64LE(offset + lane * 8);
    let lfsr = 1;
    for (let round = 0; round < 24; round++) {
      const c = Array(5).fill(0n);
      for (let x = 0; x < 5; x++) for (let y = 0; y < 5; y++) c[x] ^= state[x + 5 * y];
      for (let x = 0; x < 5; x++) {
        const d = c[(x + 4) % 5] ^ rot(c[(x + 1) % 5], 1);
        for (let y = 0; y < 5; y++) state[x + 5 * y] ^= d;
      }
      let carry = state[1];
      for (let step = 0; step < 24; step++) { const t = state[PI[step]]; state[PI[step]] = rot(carry, RHO[step]); carry = t; }
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

function encodeFile(file, kind) {
  const object = loadRaw(fs.readFileSync(file));
  if (kind === 'report') validateReport(object);
  const bytes = canonicalBytes(object);
  return { object, bytes, hash: keccak256(bytes) };
}
function verifyManifest(file) {
  const manifest = loadRaw(fs.readFileSync(file)); const root = path.dirname(file);
  check(manifest.profile === PROFILE, 'MANIFEST', 'wrong profile'); let pass = 0, fail = 0;
  for (const item of manifest.positiveVectors) {
    try {
      const { object, bytes, hash } = encodeFile(path.join(root, item.source), item.kind);
      check(bytes.equals(fs.readFileSync(path.join(root, item.canonicalUtf8))), 'BYTES_MISMATCH', item.id);
      check(bytes.toString('hex') === fs.readFileSync(path.join(root, item.canonicalHex), 'ascii').trim(), 'HEX_MISMATCH', item.id);
      check(bytes.length === item.canonicalLength && hash === item.expectedKeccak256, 'HASH_MISMATCH', item.id);
      if (item.inputSource) check(object.inputHash === encodeFile(path.join(root, item.inputSource), 'input').hash, 'INPUT_BINDING', item.id);
      pass++;
    } catch (error) { fail++; console.error(`FAIL ${item.id}: ${error.code || ''} ${error.message}`); }
  }
  for (const item of manifest.negativeVectors) {
    try { encodeFile(path.join(root, item.source), item.kind); throw new Error('invalid source accepted'); }
    catch (error) {
      if (error.code === item.expectedError) pass++;
      else { fail++; console.error(`FAIL ${item.id}: expected ${item.expectedError}, got ${error.code || error.message}`); }
    }
  }
  const hashes = new Map(manifest.positiveVectors.map(v => [v.id, v.expectedKeccak256]));
  for (const rel of manifest.hashRelations) {
    if ((hashes.get(rel.left) === hashes.get(rel.right)) === (rel.relation === 'equal')) pass++;
    else fail++;
  }
  console.log(JSON.stringify({ implementation: 'node', profile: PROFILE, pass, fail, skip: 0, independentAcceptance: 'NOT_RUN' }));
  return fail ? 1 : 0;
}
function selftest() {
  for (const [source, hash] of [
    ['', '0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'],
    ['abc', '0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45'],
  ]) check(keccak256(Buffer.from(source)) === hash, 'KAT_FAILURE', 'Ethereum Keccak KAT mismatch');
  console.log(JSON.stringify({ implementation: 'node', keccakKnownAnswerTests: 2, pass: 2, fail: 0 }));
}
function main(args) {
  try {
    if (args[0] === 'selftest') selftest();
    else if (args[0] === 'verify' && args.length === 2) return verifyManifest(args[1]);
    else if (args[0] === 'encode' && args[1]) {
      let kind = 'report', output = null;
      for (let i = 2; i < args.length; i += 2) {
        check(['--kind', '--out'].includes(args[i]) && args[i + 1], 'USAGE', 'encode source [--kind report|input|generic] [--out path]');
        if (args[i] === '--kind') kind = args[i + 1]; else output = args[i + 1];
      }
      check(['report', 'input', 'generic'].includes(kind), 'USAGE', 'unknown kind');
      const { bytes, hash } = encodeFile(args[1], kind);
      if (output) fs.writeFileSync(output, bytes);
      console.log(JSON.stringify({ profile: PROFILE, kind, canonicalLength: bytes.length, keccak256: hash,
        semanticValidation: kind === 'report' ? 'ReportBody' : 'separate input schema required' }));
    } else throw new ValidationError('USAGE', 'selftest | verify manifest | encode source [--kind report|input|generic] [--out path]');
    return 0;
  } catch (error) { console.error(JSON.stringify({ error: error.code || 'FILE_ERROR', message: error.message })); return 1; }
}
if (require.main === module) process.exitCode = main(process.argv.slice(2));
module.exports = { loadRaw, validateReport, canonicalBytes, keccak256, encodeFile, ValidationError };
