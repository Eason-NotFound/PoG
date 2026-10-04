/** Standalone TypeScript byte-parity check for the API's fixed public vectors. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

type Material = string | { [key: string]: Material };
type Vector = { kind: string; material: Material; expectedHex: string; sha256: string };

function canonical(value: Material): string {
  if (typeof value === "string") {
    assert(/^[\x20-\x7e]*$/.test(value));
    return JSON.stringify(value);
  }
  assert(value !== null && typeof value === "object" && !Array.isArray(value));
  return "{" + Object.keys(value).sort().map(key => {
    assert(/^[\x20-\x7e]+$/.test(key));
    return JSON.stringify(key) + ":" + canonical(value[key]);
  }).join(",") + "}";
}

const vectors: Vector[] = JSON.parse(readFileSync(0, "utf8"));
assert.equal(vectors.length, 2);
for (const vector of vectors) {
  const bytes = Buffer.from(canonical(vector.material), "utf8");
  assert(bytes.equals(Buffer.from(vector.expectedHex, "hex")), vector.kind);
  assert.equal(createHash("sha256").update(bytes).digest("hex"), vector.sha256);
  process.stdout.write(`${vector.kind}: TypeScript/Python exact byte parity (${bytes.length} bytes)\n`);
}
