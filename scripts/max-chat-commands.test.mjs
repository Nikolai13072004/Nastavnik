import assert from "node:assert/strict";
import test from "node:test";
import { mergeChatCommands } from "./max-chat-commands.mjs";

test("command hints add navigation without erasing existing unrelated commands", () => {
  const current = [{ name: "contact", description: "Контакты" }, { name: "start", description: "Old" }];
  const updated = mergeChatCommands(current);
  assert.deepEqual(updated.map((command) => command.name), ["start", "menu", "courses", "help", "contact"]);
  assert.equal(updated[4], current[0]);
  assert.deepEqual(mergeChatCommands(updated), updated);
  assert.equal(current[1].description, "Old");
});

test("missing hints can be created but malformed and overflowing lists fail without mutation", () => {
  assert.equal(mergeChatCommands(null).length, 4);
  assert.equal(mergeChatCommands(undefined).length, 4);
  assert.throws(() => mergeChatCommands({}), /Invalid/);
  assert.throws(() => mergeChatCommands([{ name: "other" }]), /Invalid/);
  assert.throws(() => mergeChatCommands(Array.from({ length: 32 }, (_, index) => ({
    name: `command${index}`, description: "Other",
  }))), /limit/);
});
