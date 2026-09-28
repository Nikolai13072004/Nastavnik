import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatFeedbackCodec } from "./chat-feedback-codec";
import { knowledgeFeedbackHash } from "./knowledge-feedback-proof";

const result = { answer: "Точный ответ", refused: false, sources: [] };

test("feedback snapshot is encrypted and bound to the exact user link", () => {
  const codec = createChatFeedbackCodec("test-token");
  const value = { question: "Личный вопрос", result };
  const sealed = codec.seal(value, "bot:user:link");
  assert.ok(!sealed.includes(value.question));
  assert.deepEqual(codec.open(sealed, "bot:user:link"), value);
  assert.throws(() => codec.open(sealed, "bot:other:link"));
  assert.throws(() =>
    createChatFeedbackCodec("other-token").open(sealed, "bot:user:link"),
  );
});

test("feedback proof rejects a changed question or answer and ignores receipt ID", () => {
  const hash = knowledgeFeedbackHash("Вопрос", result);
  assert.notEqual(knowledgeFeedbackHash("Другой вопрос", result), hash);
  assert.notEqual(
    knowledgeFeedbackHash("Вопрос", { ...result, answer: "Подмена" }),
    hash,
  );
  assert.equal(
    knowledgeFeedbackHash("Вопрос", { ...result, eventId: "receipt" }),
    hash,
  );
});
