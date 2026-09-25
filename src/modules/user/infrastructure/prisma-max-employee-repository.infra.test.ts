import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { PERMISSIONS, ROLE_PERMISSIONS, STANDARD_ROLE_NAMES } from "@/lib/roles";
import { createCreateMaxEmployee, type MaxEmployeeIdentity } from "../application/create-max-employee";
import { createIssueMaxEmployeeCode } from "../application/issue-max-employee-code";
import { prismaMaxEmployeeRepository } from "./prisma-max-employee-repository";

const prefix = `max-employee-test-${randomUUID()}`;
const orgId = `${prefix}-org`;
const otherOrgId = `${prefix}-other-org`;
const actorId = `${prefix}-hr`;
const maxUserId = `${prefix}-max`;
const roleName = `${prefix}-hr-role`;
const issuedTokens = ["A".repeat(32), "B".repeat(32), "C".repeat(32)];
let tokenIndex = 0;
let identity: MaxEmployeeIdentity;

const create = createCreateMaxEmployee({
  repository: prismaMaxEmployeeRepository,
  createPasswordHash: async () => "test-password-hash",
  createToken: () => issuedTokens[tokenIndex++],
  hashToken: (token) => `hash-${token}`,
  now: () => new Date("2026-09-24T10:00:00.000Z"),
});
const reissue = createIssueMaxEmployeeCode({
  repository: prismaMaxEmployeeRepository,
  createToken: () => issuedTokens[tokenIndex++],
  hashToken: (token) => `hash-${token}`,
  now: () => new Date("2026-09-24T10:01:00.000Z"),
});

before(async () => {
  await prisma.organization.createMany({ data: [
    { id: orgId, name: orgId },
    { id: otherOrgId, name: otherOrgId },
  ] });
  await prisma.roleProfile.createMany({ data: [
    { name: roleName, permissionsJson: JSON.stringify([
      PERMISSIONS.USERS_CREATE, PERMISSIONS.USERS_VIEW, PERMISSIONS.REPORTS_VIEW,
    ]) },
  ] });
  await prisma.roleProfile.upsert({
    where: { name: STANDARD_ROLE_NAMES.STUDENT },
    create: {
      name: STANDARD_ROLE_NAMES.STUDENT,
      isSystem: true,
      permissionsJson: JSON.stringify(ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.STUDENT]),
    },
    update: {
      isSystem: true,
      permissionsJson: JSON.stringify(ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.STUDENT]),
    },
  });
  await prisma.user.create({ data: {
    id: actorId,
    login: actorId,
    name: "HR test",
    passwordHash: "test-password-hash",
    role: roleName,
    organizationId: orgId,
  } });
  const link = await prisma.maxAccountLink.create({ data: { maxUserId, userId: actorId, organizationId: orgId } });
  identity = { maxUserId, userId: actorId, organizationId: orgId, linkedAt: link.createdAt.toISOString() };
});

after(async () => {
  await prisma.auditLogEvent.deleteMany({ where: { actorId } });
  await prisma.user.deleteMany({ where: { OR: [{ id: actorId }, { login: { startsWith: prefix } }] } });
  await prisma.roleProfile.deleteMany({ where: { name: { in: [roleName, STANDARD_ROLE_NAMES.STUDENT] } } });
  await prisma.organization.deleteMany({ where: { id: { in: [orgId, otherOrgId] } } });
  await prisma.$disconnect();
});

test("создание пишет сотрудника, роль, хеш кода и аудит атомарно", async () => {
  const email = `${prefix}@example.org`;
  const result = await create(identity, { firstName: "Иван", lastName: "Петров", email });
  assert.equal(result.status, "CREATED");
  if (result.status !== "CREATED") return;

  const user = await prisma.user.findUniqueOrThrow({
    where: { id: result.employee.id },
    include: { userRoles: { include: { roleProfile: true } }, maxLinkInvite: true },
  });
  assert.equal(user.organizationId, orgId);
  assert.equal(user.email, email);
  assert.equal(user.role, STANDARD_ROLE_NAMES.STUDENT);
  assert.deepEqual(user.userRoles.map(({ roleProfile }) => roleProfile.name), [STANDARD_ROLE_NAMES.STUDENT]);
  assert.equal(user.userRoles[0].roleProfile.isSystem, true);
  assert.deepEqual(
    JSON.parse(user.userRoles[0].roleProfile.permissionsJson),
    ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.STUDENT],
  );
  assert.equal(user.maxLinkInvite?.tokenHash, `hash-${result.token}`);
  assert.ok(await prisma.auditLogEvent.findFirst({ where: {
    actorId, objectId: user.id, action: "users:create",
  } }));

  assert.deepEqual(await create(identity, { firstName: "Другой", lastName: "", email }), { status: "EMAIL_EXISTS" });
  assert.equal(await prisma.user.count({ where: { email } }), 1);

  const replacement = await reissue(identity, user.id);
  assert.equal(replacement.status, "ISSUED");
  if (replacement.status !== "ISSUED") return;
  const currentCode = await prisma.maxLinkInvite.findUniqueOrThrow({ where: { userId: user.id } });
  assert.equal(currentCode.tokenHash, `hash-${replacement.token}`);
  assert.notEqual(currentCode.tokenHash, `hash-${result.token}`);

  await prisma.maxAccountLink.create({ data: { maxUserId: `${prefix}-employee-max`, userId: user.id, organizationId: orgId } });
  assert.deepEqual(await reissue(identity, user.id), { status: "NOT_FOUND" });

  const foreignEmployee = await prisma.user.create({ data: {
    login: `${prefix}-foreign`, name: "Foreign employee", passwordHash: "test-password-hash",
    role: STANDARD_ROLE_NAMES.STUDENT, organizationId: otherOrgId,
  } });
  assert.deepEqual(await reissue(identity, foreignEmployee.id), { status: "NOT_FOUND" });

  const administrator = await prisma.user.create({ data: {
    login: `${prefix}-administrator`, name: "Administrator", passwordHash: "test-password-hash",
    role: "ADMIN", organizationId: orgId,
  } });
  assert.deepEqual(await reissue(identity, administrator.id), { status: "NOT_FOUND" });
});

test("повторное создание не меняет существующие права роли ученика", async () => {
  const customPermissions = JSON.stringify([PERMISSIONS.COURSES_VIEW]);
  await prisma.roleProfile.update({
    where: { name: STANDARD_ROLE_NAMES.STUDENT },
    data: { permissionsJson: customPermissions },
  });

  await prismaMaxEmployeeRepository.transact((transaction) => transaction.createEmployee({
    firstName: "Анна",
    lastName: "",
    name: "Анна",
    email: `${prefix}-second@example.org`,
    organizationId: orgId,
    passwordHash: "test-password-hash",
  }));

  const role = await prisma.roleProfile.findUniqueOrThrow({
    where: { name: STANDARD_ROLE_NAMES.STUDENT },
  });
  assert.equal(role.permissionsJson, customPermissions);
});

test("чужая организация и отозванные права не дают создать сотрудника", async () => {
  const foreign = { ...identity, organizationId: otherOrgId };
  assert.deepEqual(await create(foreign, {
    firstName: "Павел", lastName: "", email: `${prefix}-foreign@example.org`,
  }), { status: "FORBIDDEN" });

  await prisma.roleProfile.update({ where: { name: roleName }, data: { permissionsJson: "[]" } });
  assert.deepEqual(await create(identity, {
    firstName: "Павел", lastName: "", email: `${prefix}-revoked@example.org`,
  }), { status: "FORBIDDEN" });
  assert.equal(await prisma.user.count({ where: { email: { startsWith: prefix } } }), 2);
});
