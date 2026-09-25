import assert from "node:assert/strict";
import { test } from "node:test";
import { createCreateMaxEmployee, type MaxEmployeeIdentity, type MaxEmployeeRepository,
  type MaxEmployeeTransaction } from "./create-max-employee";
import { createIssueMaxEmployeeCode } from "./issue-max-employee-code";

const identity: MaxEmployeeIdentity = {
  maxUserId: "max-1",
  userId: "hr-1",
  organizationId: "org-1",
  linkedAt: "2026-09-24T00:00:00.000Z",
};

function setup(authorized = true) {
  const calls: string[] = [];
  const transaction: MaxEmployeeTransaction = {
    async findActor() {
      calls.push("findActor");
      return authorized ? { id: "hr-1", login: "hr", name: "HR", organizationId: "org-1" } : null;
    },
    async createEmployee(input) {
      calls.push(`create:${input.organizationId}:${input.email}:${input.name}`);
      return { id: "new-1", name: input.name };
    },
    async issueLinkCode(_userId, _organizationId, tokenHash) {
      calls.push(`issue:${tokenHash}`);
    },
    async recordAudit() { calls.push("audit"); },
    async findEmployee(userId, organizationId) {
      calls.push(`findEmployee:${userId}:${organizationId}`);
      return userId === "new-1" ? { id: userId, name: "Иван Петров" } : null;
    },
    async upsertLinkCode(_userId, _organizationId, tokenHash) {
      calls.push(`reissue:${tokenHash}`);
    },
    async recordCodeAudit() { calls.push("codeAudit"); },
  };
  const repository: MaxEmployeeRepository = {
    transact: (work) => work(transaction),
    isUniqueViolation: () => false,
  };
  const create = createCreateMaxEmployee({
    repository,
    createPasswordHash: async () => { calls.push("hashPassword"); return "password-hash"; },
    createToken: () => "test-token",
    hashToken: () => "token-hash",
    now: () => new Date("2026-09-24T00:00:00.000Z"),
  });
  const reissue = createIssueMaxEmployeeCode({
    repository,
    createToken: () => "second-token",
    hashToken: () => "second-hash",
    now: () => new Date("2026-09-24T00:00:00.000Z"),
  });
  return { calls, create, reissue };
}

test("создаёт сотрудника в организации HR и выдаёт одноразовый код", async () => {
  const { calls, create } = setup();
  const result = await create(identity, { firstName: " Иван ", lastName: " Петров ", email: " IVAN@EXAMPLE.ORG " });
  assert.deepEqual(result, {
    status: "CREATED",
    employee: { id: "new-1", name: "Иван Петров" },
    token: "test-token",
    expiresAt: "2026-09-24T00:15:00.000Z",
  });
  assert.deepEqual(calls, ["findActor", "hashPassword", "create:org-1:ivan@example.org:Иван Петров", "issue:token-hash", "audit"]);
});

test("не тратит пароль и не создаёт запись без прав", async () => {
  const { calls, create } = setup(false);
  assert.deepEqual(await create(identity, { firstName: "Иван", lastName: "", email: "ivan@example.org" }), { status: "FORBIDDEN" });
  assert.deepEqual(calls, ["findActor"]);
});

test("проверяет имя и email до записи", async () => {
  const { calls, create } = setup();
  assert.deepEqual(await create(identity, { firstName: " ", lastName: "", email: "invalid" }), { status: "INVALID_INPUT" });
  assert.deepEqual(calls, []);
});

test("повторно выдаёт код только доступному сотруднику", async () => {
  const { calls, reissue } = setup();
  assert.deepEqual(await reissue(identity, "new-1"), {
    status: "ISSUED", token: "second-token", expiresAt: "2026-09-24T00:15:00.000Z",
  });
  assert.deepEqual(calls, ["findActor", "findEmployee:new-1:org-1", "reissue:second-hash", "codeAudit"]);
  assert.deepEqual(await reissue(identity, "missing"), { status: "NOT_FOUND" });
  assert.deepEqual(await reissue(identity, "!!!"), { status: "INVALID_INPUT" });
});
