import assert from "node:assert/strict";
import { test } from "node:test";

import { safeExternalUrl } from "./urls.ts";

test("acepta URLs HTTPS y las normaliza", () => {
  assert.equal(
    safeExternalUrl("https://jobs.ashbyhq.com/nubank/857cd146"),
    "https://jobs.ashbyhq.com/nubank/857cd146",
  );
  assert.equal(
    safeExternalUrl("HTTPS://Example.com/jobs?id=1#apply"),
    "https://example.com/jobs?id=1#apply",
  );
});

test("rechaza esquemas distintos de HTTPS", () => {
  for (const value of [
    "http://block.xyz/careers/jobs/5108007008",
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "ftp://example.com/job",
    "mailto:jobs@example.com",
  ]) {
    assert.equal(safeExternalUrl(value), null, value);
  }
});

test("rechaza valores que no son URLs absolutas", () => {
  for (const value of [
    "",
    "   ",
    "/jobs/123",
    "//evil.example/jobs",
    "jobs.example.com/123",
    "https://",
  ]) {
    assert.equal(safeExternalUrl(value), null, JSON.stringify(value));
  }
});

test("no deja pasar caracteres que rompan el atributo href", () => {
  const result = safeExternalUrl('https://example.com/a"><script>');

  assert.ok(result);
  assert.ok(!result.includes('"'));
  assert.ok(!result.includes("<"));
});
