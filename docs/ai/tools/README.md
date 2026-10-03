# Offline R2 reference tools

Status: **R2 interface format frozen**. The existing
[freeze record](../V2_REPORT_FREEZE_RECORD.md) and
[historical source manifest](../V2_REPORT_FREEZE_MANIFEST.json) define that lifecycle.
The original `0.2-candidate` strings remain wire identifiers. These tools perform
no model calls, chain reads, transactions, signatures or key operations; format
freeze does not certify a deployed API adapter, signer or full runtime chain.

This README is a revised public description with different bytes from its R2
historical source. A new public release identity manifest must bind its current
SHA-256 and length; the existing 162-file manifest continues to bind commit
`19657f4459186d06b0608d27362e1522db90ea46`, not this revised mirror.

The accepted API A2 comparison commit is
`4c9f1a40ac225d684d00b5abcf8081c43594bfcc`; its local ReceiptConfirmed scope does
not establish an R2 report endpoint or model/signing integration. The
[004 integration profile](../V2_A2_INTEGRATION_PROFILE_001.md) defines the first
EOA route, trusted file/page binding and durable shared nonce implementation
target. Component review, scoring policy and runtime evidence remain separate.

`cjson_reference.py` and `cjson_reference.ts` separately implement strict raw
JSON parsing, the custom PoG-CJSON-0.2 encoder, ReportBody semantic checks and Ethereum
Keccak-256. The TypeScript parser checks decoded duplicate keys before any object can
overwrite them. Python uses `object_pairs_hook` and strict numeric token hooks.
Both reject BOM, invalid UTF-8, isolated surrogates, negative/float/exponent and
non-JSON numeric tokens. Semantic BODY checks cover exact fields, integer widths,
bool-versus-int, nullable/version rules, completeness and prescribed array order.

Both reference hash implementations are local Keccak-f1600 code (Python 2-D
lanes versus TypeScript flat lanes/cyclic rho-pi with generated round constants), using
the Ethereum suffix `0x01`. `selftest` runs empty/`abc` known-answer checks. They
do not use `hashlib.sha3_256` or Node `sha3-256`. A separate Crypto.Hash.keccak
comparison is a third implementation check when recorded for the same exact
source. Passing two related reference implementations is not independent
acceptance, an actual signature or a Solidity helper test.

The TypeScript source is compiled with TypeScript 5.9.3, `--strict` and
`--noEmitOnError`. `cjson_reference.cjs` is the previous JavaScript reference,
updated for R2 compatibility; running it does not count as TypeScript evidence.
PoG-CJSON-0.2 is fully specified in the appendix; it is not RFC 8785/JCS.

From the repository root, using Python 3 and Node with ES2022/BigInt support.
Install exact compiler tooling only into a separate temporary directory or use
an existing review toolchain; do not modify business repository dependencies:

```text
npm install --prefix /tmp/pog-review-tools --ignore-scripts --save-exact typescript@5.9.3 @types/node@25.9.5
node /tmp/pog-review-tools/node_modules/typescript/lib/tsc.js --strict --target ES2022 --module commonjs --lib ES2022 --types node --typeRoots /tmp/pog-review-tools/node_modules/@types --skipLibCheck --noEmitOnError --outDir /tmp/pog-review-tools/compiled docs/ai/tools/cjson_reference.ts
python docs/ai/tools/cjson_reference.py selftest
node /tmp/pog-review-tools/compiled/cjson_reference.js selftest
python docs/ai/tools/cjson_reference.py verify docs/ai/vectors/v2/manifest.json
node /tmp/pog-review-tools/compiled/cjson_reference.js verify docs/ai/vectors/v2/manifest.json
python docs/ai/tools/build_vectors.py
python docs/ai/tools/cjson_reference.py encode report.json --kind report --out report.cjson
node /tmp/pog-review-tools/compiled/cjson_reference.js encode input.json --kind input --out input.cjson
```

Replace `/tmp/pog-review-tools` with a private review tools directory on Windows.
`--skipLibCheck` excludes third-party declarations only; the reference source is
strictly type-checked. Compiler output stays outside the repository. These
offline commands produce no prepared signing requests or signatures. Running
them verifies their exact source and fixtures; it does not alter the format
freeze or grant runtime, production-key or payment approval.

`verify` independently derives bytes/hash from every vector's source JSON and
checks the stored `.cjson`, `.hex`, length and expected hash. Every report vector
that declares `inputSource` also binds its input hash. Negative raw sources are
intentionally invalid files; do not parse them with a bulk JSON formatter.
`build_vectors.py` writes only `docs/ai/vectors/v2` and regenerates from the
reviewed example bundles; regeneration requires both verifiers to rerun.

`--kind input` and `--kind generic` validate tokens/scalars/byte limits and encode
the **whole provided object**, with no field removal or self-hash insertion.
They do **not** validate the input business schema, external authority, document
bytes, deployment or chain snapshot. Validate input separately with
`schemas/v2/input.schema.json`. They do not silently sort any array.

`.cjson` is the exact UTF-8 payload without BOM or terminal newline. The source
`.json` and hex presentation have readable whitespace/newlines that are excluded
from the payload. Text is never Unicode-normalized. Hashes of NFC/NFD therefore
differ; source object-key order and equivalent JSON string escapes do not.

Nonpaged image EvidenceRef.pages is `null`; Locator.page must also be `null`.
`Photo` always uses this shape. Other business kinds can be images too, so their
trusted MIME/input mapping needs a separate gate. PDF report `pages:[]` means
no selected PDF page; it never means nonpaged and forbids numeric locators.
Image `pages:null` and PDF `pages:[]` have different canonical bytes/hash.

The scored stage fixtures contain all business dependencies as saved synthetic
snapshot presets. `1000` is a fixed serialization test value and
`serialization-test-score-v1` is not an approved production scoring policy.
`executionMode=synthetic_fixture`, `registrySnapshot.verified=false`, no model
execution, and production signing prohibition apply even to `complete` fixtures.
A real complete, score-policy-approved, independently signable/accepted positive
remains **NOT_RUN**. `receiptDigest` stays a distinct synthetic typed commitment;
a receipt file hash does not become a Recipient signature proof.
