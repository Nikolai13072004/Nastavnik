import assert from "node:assert/strict";
import { randomUUID, createHash, randomBytes, createHmac } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { createMaxAccountLinking, MaxLinkError } from "../application/link-account";
import { prismaMaxLinkRepository as repository } from "./prisma-max-link-repository";
import { handleMaxIdentity } from "./identity-handler";
import { findMaxSessionIdentity, prismaMaxLearnerRepository } from "./prisma-max-learner-repository";
import { createListMaxCourses } from "../application/list-courses";
import { createMaxSessionCodec } from "./learner-session";
import { handleMaxCourses } from "./courses-handler";

// All records are uniquely owned by this test run; no seed/reset or broad delete.
const prefix = `max-test-${randomUUID()}`;
const userIds: string[] = [];
const courseIds: string[] = [];
const groupIds: string[] = [];
const orgId = `${prefix}-org`;
const otherOrgId = `${prefix}-other-org`;
let sequence = 0;
const linking = createMaxAccountLinking({
  repository,
  now: () => new Date(),
  createToken: () => randomBytes(24).toString("base64url"),
  hashToken: (token) => createHash("sha256").update(token).digest("hex"),
});

before(async () => {
  await prisma.organization.createMany({ data: [{ id: orgId, name: orgId }, { id: otherOrgId, name: otherOrgId }] });
});

after(async () => {
  await prisma.user.deleteMany({ where: { id: { in: userIds } } });
  await prisma.course.deleteMany({ where: { id: { in: courseIds } } });
  await prisma.group.deleteMany({ where: { id: { in: groupIds } } });
  await prisma.organization.deleteMany({ where: { id: { in: [orgId, otherOrgId] } } });
  await prisma.$disconnect();
});

async function employee(status = "ACTIVE", organizationId: string | null = orgId) {
  const id = `${prefix}-${++sequence}`;
  userIds.push(id);
  return prisma.user.create({ data: { id, login: id, name: "MAX test employee", passwordHash: "disabled-test-account", status, organizationId } });
}

function isLinkError(code: string) {
  return (error: unknown) => error instanceof MaxLinkError && error.code === code;
}

test("links once, stores no raw code, and refuses replay", async () => {
  const user = await employee();
  const invite = await linking.issue(user.id);
  const stored = await prisma.maxLinkInvite.findUniqueOrThrow({ where: { userId: user.id } });
  assert.notEqual(stored.tokenHash, invite.token);
  assert.equal(stored.tokenHash, createHash("sha256").update(invite.token).digest("hex"));
  await linking.accept(invite.token, `${prefix}-max1`);
  assert.deepEqual(await linking.findEmployee(`${prefix}-max1`), { name: user.name, organizationName: orgId });
  await assert.rejects(linking.accept(invite.token, `${prefix}-max2`), isLinkError("INVALID_INVITE"));
  await assert.rejects(linking.issue(user.id), isLinkError("ALREADY_LINKED"));
});

test("reissuing invalidates the old code; expiry is enforced", async () => {
  const user = await employee();
  const old = await linking.issue(user.id);
  const current = await linking.issue(user.id);
  await assert.rejects(linking.accept(old.token, `${prefix}-reissue`), isLinkError("INVALID_INVITE"));
  await prisma.maxLinkInvite.update({ where: { userId: user.id }, data: { expiresAt: new Date(0) } });
  await assert.rejects(linking.accept(current.token, `${prefix}-expired`), isLinkError("INVALID_INVITE"));
});

test("inactive users and users without an organization cannot issue codes", async () => {
  for (const status of ["PENDING", "BLOCKED", "ARCHIVED"]) {
    await assert.rejects(linking.issue((await employee(status)).id), isLinkError("UNAVAILABLE"));
  }
  await assert.rejects(linking.issue((await employee("ACTIVE", null)).id), isLinkError("UNAVAILABLE"));
});

test("blocks acceptance after revocation or organization change", async () => {
  for (const update of [{ status: "BLOCKED" }, { organizationId: otherOrgId }]) {
    const user = await employee();
    const invite = await linking.issue(user.id);
    await prisma.user.update({ where: { id: user.id }, data: update });
    await assert.rejects(linking.accept(invite.token, `${prefix}-${user.id}`), isLinkError("INVALID_INVITE"));
  }
});

test("never silently rebinds a MAX profile and rolls back the losing invitation", async () => {
  const first = await employee();
  const second = await employee();
  const maxId = `${prefix}-conflict`;
  await linking.accept((await linking.issue(first.id)).token, maxId);
  const secondInvite = await linking.issue(second.id);
  await assert.rejects(linking.accept(secondInvite.token, maxId), isLinkError("ALREADY_LINKED"));
  assert.ok(await prisma.maxLinkInvite.findUnique({ where: { userId: second.id } }));
  await linking.accept(secondInvite.token, `${maxId}-other`);
});

test("two simultaneous consumers produce exactly one binding", async () => {
  const user = await employee();
  const invite = await linking.issue(user.id);
  const outcomes = await Promise.allSettled([
    linking.accept(invite.token, `${prefix}-race1`),
    linking.accept(invite.token, `${prefix}-race2`),
  ]);
  assert.equal(outcomes.filter((result) => result.status === "fulfilled").length, 1);
  assert.equal(await prisma.maxAccountLink.count({ where: { userId: user.id } }), 1);
  assert.equal(await prisma.maxLinkInvite.count({ where: { userId: user.id } }), 0);
});

test("a stored link stops resolving when the employee is blocked or moved", async () => {
  const user = await employee();
  const maxId = `${prefix}-revoked`;
  await linking.accept((await linking.issue(user.id)).token, maxId);
  await prisma.user.update({ where: { id: user.id }, data: { status: "BLOCKED" } });
  assert.equal(await linking.findEmployee(maxId), null);
  await prisma.user.update({ where: { id: user.id }, data: { status: "ACTIVE", organizationId: otherOrgId } });
  assert.equal(await linking.findEmployee(maxId), null);
});

test("two employees cannot concurrently claim the same MAX identity", async () => {
  const first = await employee();
  const second = await employee();
  const a = await linking.issue(first.id);
  const b = await linking.issue(second.id);
  const maxId = `${prefix}-same-max-race`;
  const outcomes = await Promise.allSettled([linking.accept(a.token, maxId), linking.accept(b.token, maxId)]);
  assert.equal(outcomes.filter((result) => result.status === "fulfilled").length, 1);
  assert.equal(await prisma.maxAccountLink.count({ where: { maxUserId: maxId } }), 1);
  assert.equal(await prisma.maxLinkInvite.count({ where: { userId: { in: [first.id, second.id] } } }), 1);
});

test("signed HTTP flow links employee; forged identity cannot consume an invite", async () => {
  const user = await employee();
  const invite = await linking.issue(user.id);
  const params = new URLSearchParams({ auth_date: String(Math.floor(Date.now() / 1000)), user: JSON.stringify({ id: 123456789, first_name: "Test" }) });
  params.sort();
  const botToken = randomBytes(24).toString("hex");
  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const hash = createHmac("sha256", secret).update([...params].map(([key, value]) => `${key}=${value}`).join("\n")).digest("hex");
  params.set("hash", hash);
  const request = (initData: string) => new Request("http://localhost/api/max/identity", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initData, inviteToken: invite.token }),
  });
  assert.equal((await handleMaxIdentity(request("forged"), botToken, linking)).status, 401);
  assert.ok(await prisma.maxLinkInvite.findUnique({ where: { userId: user.id } }));
  const response = await handleMaxIdentity(request(params.toString()), botToken, linking);
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).employee, { name: user.name, organizationName: orgId });
  assert.equal(response.headers.get("set-cookie"), null);
});

test("MAX sessions see assigned published courses only, including group access and explicit expiry", async () => {
  const user = await employee();
  // Administrator rights must not widen the learner-only API scope.
  await prisma.user.update({ where: { id: user.id }, data: { role: "ADMIN" } });
  const other = await employee();
  const maxId = `${prefix}-courses`;
  await linking.accept((await linking.issue(user.id)).token, maxId);
  const groupId = `${prefix}-course-group`;
  groupIds.push(groupId);
  await prisma.group.create({ data: { id: groupId, name: groupId, memberships: { create: { userId: user.id } } } });
  const scenarios = ["direct", "group", "expired-direct", "draft", "other-user", "foreign-org", "unassigned"];
  for (const kind of scenarios) {
    const id = `${prefix}-course-${kind}`;
    courseIds.push(id);
    await prisma.course.create({ data: {
      id,
      title: kind,
      status: kind === "draft" ? "DRAFT" : "PUBLISHED",
      organizationId: kind === "foreign-org" ? otherOrgId : orgId,
    } });
    if (["direct", "expired-direct", "draft", "other-user", "foreign-org"].includes(kind)) {
      await prisma.courseUserAssignment.create({ data: {
        courseId: id, userId: kind === "other-user" ? other.id : user.id,
        expiresAt: kind === "expired-direct" ? new Date(0) : null,
      } });
    }
    if (["group", "expired-direct"].includes(kind)) {
      await prisma.courseGroupAssignment.create({ data: { courseId: id, groupId } });
    }
  }
  const principal = await findMaxSessionIdentity(maxId);
  assert.ok(principal);
  const codec = createMaxSessionCodec("courses-test-bot");
  const session = codec.issue(principal);
  const list = createListMaxCourses(prismaMaxLearnerRepository);
  const request = () => new Request("http://localhost/api/max/courses", { headers: { Authorization: `Bearer ${session.token}` } });
  const response = await handleMaxCourses(request(), "courses-test-bot", list);
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).courses.map((course: { title: string }) => course.title).sort(), ["direct", "group"]);
  await prisma.user.update({ where: { id: user.id }, data: { status: "BLOCKED" } });
  assert.equal((await handleMaxCourses(request(), "courses-test-bot", list)).status, 401);
  await prisma.user.update({ where: { id: user.id }, data: { status: "ACTIVE", organizationId: otherOrgId } });
  assert.equal((await handleMaxCourses(request(), "courses-test-bot", list)).status, 401);
  await prisma.maxAccountLink.update({ where: { maxUserId: maxId }, data: { organizationId: otherOrgId } });
  const movedIdentity = await findMaxSessionIdentity(maxId);
  assert.ok(movedIdentity);
  const movedSession = codec.issue(movedIdentity);
  const movedRequest = new Request("http://localhost/api/max/courses", {
    headers: { Authorization: `Bearer ${movedSession.token}` },
  });
  const movedResponse = await handleMaxCourses(movedRequest, "courses-test-bot", list);
  assert.deepEqual((await movedResponse.json()).courses.map((course: { title: string }) => course.title), ["foreign-org"]);
});
