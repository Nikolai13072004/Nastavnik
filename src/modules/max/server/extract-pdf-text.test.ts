import assert from "node:assert/strict";
import { test } from "node:test";
import { extractPdfText, MAX_PDF_BYTES } from "./extract-pdf-text";

test("rejects malformed and oversized PDF data before invoking conversion tools", async () => {
  assert.deepEqual(await extractPdfText("not base64"), { error: "INVALID_PDF" });
  assert.deepEqual(await extractPdfText(Buffer.from("not a PDF").toString("base64")), { error: "INVALID_PDF" });
  assert.deepEqual(await extractPdfText(Buffer.alloc(MAX_PDF_BYTES + 1).toString("base64")), { error: "PDF_TOO_LARGE" });
});
