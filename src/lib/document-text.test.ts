import assert from "node:assert/strict";
import { test } from "node:test";
import { documentBlocks, readableDocumentText } from "./document-text";

test("Markdown documents have headings, paragraphs and lists without changing the original", () => {
  const original =
    "# Первый день\r\n\r\n## Доступ\r\nПолучите **свой код**.\r\n\r\n- Откройте MAX\r\n- Введите `код`\r\n\r\n1. Читайте\r\n2. Пройдите тест";
  const blocks = documentBlocks(original);
  assert.deepEqual(blocks.slice(0, 3), [
    { type: "heading", text: "Первый день" },
    { type: "heading", text: "Доступ" },
    { type: "paragraph", text: "Получите свой код." },
  ]);
  assert.deepEqual(blocks[3], {
    type: "list",
    ordered: false,
    items: ["Откройте MAX", "Введите код"],
  });
  assert.deepEqual(blocks[4], {
    type: "list",
    ordered: true,
    items: ["Читайте", "Пройдите тест"],
  });
  assert.match(original, /## Доступ/);
  assert.doesNotMatch(readableDocumentText(original), /# |\*\*|`код`/);
});

test("literal hashes, API identifiers and code examples are preserved", () => {
  const text =
    "Номер #42, язык C# и ключ API_KEY.\n\n```text\n# literal\n**not emphasis**\n```";
  assert.deepEqual(documentBlocks(text), [
    { type: "paragraph", text: "Номер #42, язык C# и ключ API_KEY." },
    { type: "code", text: "# literal\n**not emphasis**" },
  ]);
});

test("HTML is kept as inert text and Markdown links are never converted to executable HTML", () => {
  const text = "<script>alert(1)</script>\n\n[Источник](javascript:alert)";
  assert.deepEqual(documentBlocks(text), [
    { type: "paragraph", text: "<script>alert(1)</script>" },
    { type: "paragraph", text: "Источник" },
  ]);
});

test("plain text and unclosed code fences remain readable", () => {
  assert.equal(
    readableDocumentText("Обычный текст\nвторая строка\n\nДругой абзац"),
    "Обычный текст\nвторая строка\n\nДругой абзац",
  );
  assert.deepEqual(documentBlocks("```\n# literal"), [
    { type: "code", text: "# literal" },
  ]);
});
