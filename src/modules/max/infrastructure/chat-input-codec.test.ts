import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatInputCodec } from "./chat-input-codec";

test("queued input is encrypted, bound to its event and authenticated with a separate purpose key", () => {
  const codec = createChatInputCodec("fixture-secret");
  const input = { type: "text", text: "private question", messageId: "mid" } as const;
  const sealed = codec.seal(input, "event");
  assert.ok(!sealed.includes("private"));
  assert.deepEqual(codec.open(sealed, "event"), input);
  assert.notEqual(sealed, codec.seal(input, "event"));
  assert.throws(() => codec.open(sealed, "another-event"));
  assert.throws(() => createChatInputCodec("different-secret").open(sealed, "event"));
  assert.throws(() => codec.open(`${sealed.slice(0, 30)}x${sealed.slice(31)}`, "event"));
});
