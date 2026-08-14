import canonicalFixture from "../../../../contracts/fixtures/v0.3/canonical-hash.json";
import { describe, expect, it } from "vitest";

import { canonicalJson, canonicalSha256 } from "./v03Canonical";

describe("v0.3 canonical JSON", () => {
  it("matches the Python canonical bytes and SHA-256 fixture", async () => {
    expect(canonicalJson(canonicalFixture.value)).toBe(canonicalFixture.canonical_json);
    await expect(canonicalSha256(canonicalFixture.value)).resolves.toBe(canonicalFixture.sha256);
  });

  it("rejects values JSON.stringify would silently coerce", () => {
    expect(() => canonicalJson({ value: Number.NaN })).toThrow("finite numbers");
    expect(() => canonicalJson({ value: undefined })).toThrow("unsupported canonical JSON value");
    expect(() => canonicalJson("\ud800")).toThrow("lone UTF-16 surrogates");
  });
});
