# PoG-CJSON-0.2 candidate vectors

Read `manifest.json` for each source object's exact canonical UTF-8 artifact,
hex artifact, byte length and expected Ethereum Keccak-256. The profile name
is PoG-CJSON-0.2, the custom profile fully defined in the appendix;
ReportBody/input schema versions are 0.2-candidate. Status: API CHANGE_REQUIRED,
not frozen; bounded signing test not approved.

The two stages include an incomplete Review/null report and a separate complete
synthetic dependency fixture with scored ReportBody. All dependency bytes are
saved under `dependency-data`; SHA-256/Keccak entries bind those exact file bytes.
Reports bind full input snapshots via `inputHash`. Fixture completeness and a
score of 1000 are test presets, not evidence of model execution, live chain facts,
approved scoring policy or authority to sign. Production SigningEnvelope must
remain blocked; there are no private keys, typed-data signatures or transactions.

Coverage includes Chinese/emoji, NFC versus NFD, U+2028/2029, every U+0000..001F
control, quotes/slash/backslash, source key order and escaped string equivalence,
empty arrays/null locators, uint64 maxima and Keccak padding boundaries.
Image references use `pages:null` with `Locator.page:null`; PDF report
`pages:[]` means no selected page, with a different hash. Invoice kind alone
does not imply PDF MIME. Photo arrays and numeric locators into null/empty page
selections are rejected; trustworthy input MIME linking is checked separately.
Raw negative files cover duplicate/escaped duplicate keys, BOM/invalid UTF-8,
surrogates, number token violations, bool-as-integer, overflow, array order,
unknown/missing fields, unknown evidence kind/execution mode, mode/version
inconsistency, required-stage emptiness and resource limits.

Generic arrays illustrate byte/hash sensitivity to order. BODY semantic arrays
must satisfy their prescribed order and are rejected if rearranged; receivers
must not repair them by sorting.

Reproduce with Python and strict compiled TypeScript commands in
[tool instructions](../../tools/README.md). A legacy CJS pass is JavaScript
compatibility evidence, not TypeScript evidence. Two reference passes are owner
verification only. Third-party Keccak, JSON schema,
Solidity helper/signature/chain checks and independent acceptance are separate
evidence; protocol signed-positive/real-model checks remain NOT_RUN.
