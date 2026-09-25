import assert from "node:assert/strict";
import { test } from "node:test";
import { createManageMaxDocuments, type MaxDocumentCommands } from "./manage-course-documents";

const identity = { maxUserId: "max", userId: "hr", organizationId: "org", linkedAt: "time" };
const valid = { courseId: "course", title: "Регламент", sourceName: "rules.txt", contentText: "Рабочий порядок." };

test("validates text documents before any persistence call", async () => {
  let calls = 0;
  const commands: MaxDocumentCommands = {
    async upload() { calls++; return "CREATED"; },
    async setPublished() { calls++; return "UPDATED"; },
  };
  const manage = createManageMaxDocuments(commands);

  assert.equal(await manage.upload(identity, { ...valid, sourceName: "rules.docx" }), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, contentText: "  " }), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, contentText: "x".repeat(32 * 1024 + 1) }), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, contentText: "bad\0text" }), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, contentText: "bad\uFFFDtext" }), "INVALID_INPUT");
  assert.equal(await manage.setPublished(identity, "", "doc", true), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, supersedesId: "previous" }), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, changeSummary: "Without a prior document" }), "INVALID_INPUT");
  assert.equal(await manage.upload(identity, { ...valid, supersedesId: "previous",
    changeSummary: "New process", checkQuestion: "Who approves?", checkOptions: ["Me", "Manager", "HR"],
    checkCorrectIndex: 3 }), "INVALID_INPUT");
  assert.equal(await manage.setPublished(identity, "course", "doc", true, ["same", "same"]), "INVALID_INPUT");
  assert.equal(calls, 0);

  assert.equal(await manage.upload(identity, { ...valid, contentText: " A\r\nB " }), "CREATED");
  assert.equal(calls, 1);
  assert.equal(await manage.upload(identity, { ...valid, sourceName: "rules.pdf" }), "CREATED");
  assert.equal(calls, 2);
  assert.equal(await manage.upload(identity, { ...valid, supersedesId: "previous",
    changeSummary: "New process", checkQuestion: "Who approves?", checkOptions: ["Me", "Manager", "HR"],
    checkCorrectIndex: 1 }), "CREATED");
  assert.equal(calls, 3);
});
