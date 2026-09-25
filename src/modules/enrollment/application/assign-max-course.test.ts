import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createAssignMaxCourse,
  type MaxAssignmentActor,
  type MaxAssignmentIdentity,
  type MaxAssignmentTransaction,
} from "./assign-max-course";

const identity: MaxAssignmentIdentity = {
  maxUserId: "max-1",
  userId: "hr-1",
  organizationId: "org-1",
  linkedAt: "2026-09-24T00:00:00.000Z",
};
const actor: MaxAssignmentActor = { id: "hr-1", login: "hr", name: "HR", organizationId: "org-1" };
const now = new Date("2026-09-24T00:00:00.000Z");

function fixture(options: {
  actor?: MaxAssignmentActor | null;
  course?: { id: string; title: string } | null;
  learner?: { id: string } | null;
  expiry?: Date | null;
  hasDirect?: boolean;
  saved?: boolean;
} = {}) {
  const calls: string[] = [];
  const transaction: MaxAssignmentTransaction = {
    findActor: async () => options.actor === undefined ? actor : options.actor,
    findCourse: async () => options.course === undefined ? { id: "course-1", title: "Курс" } : options.course,
    findLearner: async () => options.learner === undefined ? { id: "learner-1" } : options.learner,
    directExpiry: async () => options.hasDirect ? options.expiry ?? null : undefined,
    createDirect: async () => { calls.push("create"); return options.saved ?? true; },
    renewDirect: async () => { calls.push("renew"); return options.saved ?? true; },
    assignCurrentDocumentTrainings: async () => { calls.push("training"); },
    recordAudit: async (_actor, _course, _learner, renewed) => { calls.push(renewed ? "audit-renew" : "audit-create"); },
  };
  const assign = createAssignMaxCourse({ transact: async (work) => work(transaction) }, () => now);
  return { assign, calls };
}

test("MAX assignment denies missing HR access before reading course or writing", async () => {
  const { assign, calls } = fixture({ actor: null });
  assert.deepEqual(await assign(identity, "course-1", "learner-1"), { status: "FORBIDDEN" });
  assert.deepEqual(calls, []);
});

test("MAX assignment hides unavailable course and learner", async () => {
  for (const options of [{ course: null }, { learner: null }]) {
    const { assign, calls } = fixture(options);
    assert.deepEqual(await assign(identity, "course-1", "learner-1"), { status: "NOT_FOUND" });
    assert.deepEqual(calls, []);
  }
});

test("MAX assignment writes one direct assignment and audit", async () => {
  const { assign, calls } = fixture();
  assert.deepEqual(await assign(identity, "course-1", "learner-1"), { status: "ASSIGNED" });
  assert.deepEqual(calls, ["create", "training", "audit-create"]);
});

test("MAX assignment renews expired access, but does not duplicate active access", async () => {
  const expired = fixture({ hasDirect: true, expiry: new Date(0) });
  assert.deepEqual(await expired.assign(identity, "course-1", "learner-1"), { status: "RENEWED" });
  assert.deepEqual(expired.calls, ["renew", "training", "audit-renew"]);

  const active = fixture({ hasDirect: true, expiry: new Date("2027-01-01T00:00:00Z") });
  assert.deepEqual(await active.assign(identity, "course-1", "learner-1"), { status: "ALREADY_ASSIGNED" });
  assert.deepEqual(active.calls, []);

  const duplicate = fixture({ saved: false });
  assert.deepEqual(await duplicate.assign(identity, "course-1", "learner-1"), { status: "ALREADY_ASSIGNED" });
  assert.deepEqual(duplicate.calls, ["create"]);
});
