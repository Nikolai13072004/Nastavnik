/** One-off HTTP smoke test of the isolated MAX pilot, with temporary actors. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";

const prisma = new PrismaClient();
const suffix = randomUUID();
const organizationId = "max-pilot-demo-org";
const learnerId = "max-pilot-demo-learner";
const probeLearnerId = `max-report-check-learner-${suffix}`;
const courseId = "max-pilot-assessment-course";
const employeeEmail = `pilot-check-${suffix}@example.org`;
const documentSourceName = `pilot-check-${suffix}.txt`;
const pdfSourceName = `pilot-check-${suffix}.pdf`;
let createdEmployeeId: string | null = null;
let createdDocumentId: string | null = null;
let createdPdfDocumentId: string | null = null;
const actors = [
  { id: `max-report-check-hr-${suffix}`, role: "HR", maxUserId: `max-report-check-hr-${suffix}` },
  { id: `max-report-check-user-${suffix}`, role: "USER", maxUserId: `max-report-check-user-${suffix}` },
];

async function main() {
  const botToken = process.env.MAX_BOT_TOKEN;
  if (process.env.MAX_LMS_LINKS !== "disabled" || !botToken) {
    throw new Error("This check is restricted to the configured MAX pilot");
  }

  const [learner, course] = await Promise.all([
    prisma.user.findUnique({ where: { id: learnerId }, select: { organizationId: true, status: true } }),
    prisma.course.findUnique({ where: { id: courseId }, select: { organizationId: true, status: true } }),
  ]);
  if (learner?.organizationId !== organizationId || learner.status !== "ACTIVE" ||
      course?.organizationId !== organizationId || course.status !== "PUBLISHED") {
    throw new Error("Demo data does not match the expected pilot scope");
  }

  const codec = createMaxSessionCodec(botToken);
  await prisma.user.create({ data: {
    id: probeLearnerId,
    login: probeLearnerId,
    name: "Временный сотрудник для проверки назначения",
    passwordHash: "disabled-pilot-check",
    organizationId,
  } });
  for (const actor of actors) {
    await prisma.user.create({ data: {
      id: actor.id,
      login: actor.id,
      name: actor.role === "HR" ? "Временный HR тест" : "Временный сотрудник тест",
      passwordHash: "disabled-pilot-check",
      role: actor.role,
      organizationId,
    } });
    await prisma.maxAccountLink.create({ data: {
      maxUserId: actor.maxUserId,
      userId: actor.id,
      organizationId,
    } });
  }
  const probeLink = await prisma.maxAccountLink.create({ data: {
    maxUserId: probeLearnerId,
    userId: probeLearnerId,
    organizationId,
  } });
  const { token: learnerToken } = codec.issue({
    maxUserId: probeLearnerId,
    userId: probeLearnerId,
    organizationId,
    linkedAt: probeLink.createdAt.toISOString(),
  });

  async function requestAs(actor: typeof actors[number], targetCourseId?: string) {
    const link = await prisma.maxAccountLink.findUniqueOrThrow({ where: { maxUserId: actor.maxUserId } });
    const { token } = codec.issue({
      maxUserId: actor.maxUserId,
      userId: actor.id,
      organizationId,
      linkedAt: link.createdAt.toISOString(),
    });
    const query = targetCourseId ? `?courseId=${encodeURIComponent(targetCourseId)}` : "";
    return fetch(`http://127.0.0.1:3000/api/max/manager-report${query}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  }

  const reportResponse = await requestAs(actors[0], courseId);
  assert.equal(reportResponse.status, 200);
  const result = await reportResponse.json() as {
    courses: Array<{ id: string }>;
    report: { learners: Array<{ id: string; completed: boolean; passedQuizzes: number }> };
  };
  assert.ok(result.courses.some((item) => item.id === courseId));
  assert.ok(result.report.learners.some((item) =>
    item.id === learnerId && item.completed && item.passedQuizzes === 1));

  assert.equal((await requestAs(actors[1], courseId)).status, 403);
  assert.equal((await requestAs(actors[0], "course-outside-pilot-organization")).status, 404);

  const link = await prisma.maxAccountLink.findUniqueOrThrow({ where: { maxUserId: actors[0].maxUserId } });
  const { token } = codec.issue({
    maxUserId: actors[0].maxUserId,
    userId: actors[0].id,
    organizationId,
    linkedAt: link.createdAt.toISOString(),
  });
  const assignmentRequest = () => fetch("http://127.0.0.1:3000/api/max/manager-report", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ courseId, learnerId: probeLearnerId }),
  });
  assert.deepEqual(await (await assignmentRequest()).json(), { status: "ASSIGNED" });
  assert.deepEqual(await (await assignmentRequest()).json(), { status: "ALREADY_ASSIGNED" });
  assert.equal(await prisma.courseUserAssignment.count({ where: { courseId, userId: probeLearnerId } }), 1);

  const employeeResponse = await fetch("http://127.0.0.1:3000/api/max/employees", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ firstName: "Временный", lastName: "Сотрудник", email: employeeEmail }),
  });
  assert.equal(employeeResponse.status, 201);
  const employeeResult = await employeeResponse.json() as {
    status: string;
    employee: { id: string };
    token: string;
  };
  createdEmployeeId = employeeResult.employee.id;
  assert.equal(employeeResult.status, "CREATED");
  const createdEmployee = await prisma.user.findUniqueOrThrow({ where: { id: createdEmployeeId },
    select: { organizationId: true, role: true, maxLinkInvite: { select: { tokenHash: true } } } });
  assert.equal(createdEmployee.organizationId, organizationId);
  assert.equal(createdEmployee.role, "Ученик");
  assert.notEqual(createdEmployee.maxLinkInvite?.tokenHash, employeeResult.token);

  const codeResponse = await fetch("http://127.0.0.1:3000/api/max/employees", {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ userId: createdEmployeeId }),
  });
  assert.equal(codeResponse.status, 200);
  const codeResult = await codeResponse.json() as { status: string; token: string };
  assert.equal(codeResult.status, "ISSUED");
  assert.notEqual(codeResult.token, employeeResult.token);
  assert.equal((await requestAs(actors[1], courseId)).status, 403);

  const documentHeaders = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const learnerHeaders = { Authorization: `Bearer ${learnerToken}` };
  const documentBase = "http://127.0.0.1:3000/api/max/documents";
  const documentQuery = `?courseId=${encodeURIComponent(courseId)}`;
  const draftText = "Временный документ для проверки доступа.";
  const upload = await fetch(documentBase, {
    method: "POST",
    headers: documentHeaders,
    body: JSON.stringify({ courseId, title: "Проверка документа", sourceName: documentSourceName, contentText: draftText }),
  });
  assert.equal(upload.status, 201);
  const document = await prisma.maxCourseDocument.findFirstOrThrow({
    where: { organizationId, courseId, sourceName: documentSourceName },
    select: { id: true },
  });
  createdDocumentId = document.id;
  const readUrl = `${documentBase}${documentQuery}&documentId=${encodeURIComponent(document.id)}`;
  assert.deepEqual(await (await fetch(`${documentBase}${documentQuery}`, { headers: learnerHeaders })).json(), { documents: [] });
  assert.equal((await fetch(readUrl, { headers: learnerHeaders })).status, 404);
  assert.equal((await fetch(`${documentBase}?courseId=course-outside-pilot-organization`, { headers: learnerHeaders })).status, 403);
  assert.equal((await fetch(documentBase, {
    method: "POST",
    headers: { ...learnerHeaders, "Content-Type": "application/json" },
    body: JSON.stringify({ courseId, title: "No access", sourceName: "denied.txt", contentText: draftText }),
  })).status, 403);

  const changePublication = (publish: boolean) => fetch(documentBase, {
    method: "PUT",
    headers: documentHeaders,
    body: JSON.stringify({ courseId, documentId: document.id, publish }),
  });
  assert.equal((await changePublication(true)).status, 200);
  const published = await fetch(readUrl, { headers: learnerHeaders });
  assert.equal(published.status, 200);
  assert.equal((await published.json() as { document: { contentText: string } }).document.contentText, draftText);
  assert.equal((await changePublication(false)).status, 200);
  assert.equal((await fetch(readUrl, { headers: learnerHeaders })).status, 404);

  const pdfBytes = readFileSync(new URL("../public/presentations/test-1c-accounting-overview.pdf", import.meta.url));
  const pdfUpload = await fetch(documentBase, {
    method: "POST",
    headers: documentHeaders,
    body: JSON.stringify({
      courseId,
      title: "Проверка PDF",
      sourceName: pdfSourceName,
      fileBase64: pdfBytes.toString("base64"),
    }),
  });
  assert.equal(pdfUpload.status, 201);
  const pdfDocument = await prisma.maxCourseDocument.findFirstOrThrow({
    where: { organizationId, courseId, sourceName: pdfSourceName },
    select: { id: true, contentText: true },
  });
  createdPdfDocumentId = pdfDocument.id;
  assert.match(pdfDocument.contentText, /Accounting Test/);
  const pdfReadUrl = `${documentBase}${documentQuery}&documentId=${encodeURIComponent(pdfDocument.id)}`;
  assert.equal((await fetch(pdfReadUrl, { headers: learnerHeaders })).status, 404);
  const setPdfPublished = (publish: boolean) => fetch(documentBase, {
    method: "PUT",
    headers: documentHeaders,
    body: JSON.stringify({ courseId, documentId: pdfDocument.id, publish }),
  });
  assert.equal((await setPdfPublished(true)).status, 200);
  const pdfRead = await fetch(pdfReadUrl, { headers: learnerHeaders });
  assert.equal(pdfRead.status, 200);
  assert.match((await pdfRead.json() as { document: { contentText: string } }).document.contentText, /Accounting Test/);
  assert.equal((await setPdfPublished(false)).status, 200);
  assert.equal((await fetch(pdfReadUrl, { headers: learnerHeaders })).status, 404);
  console.log("MAX_MANAGER_PILOT_CHECK_OK");
}

async function run() {
  try {
    await main();
  } finally {
    try {
      const createdEmployee = await prisma.user.findUnique({ where: { login: employeeEmail }, select: { id: true } });
      const employeeId = createdEmployeeId ?? createdEmployee?.id ?? null;
      const createdDocument = await prisma.maxCourseDocument.findFirst({
        where: { organizationId, courseId, sourceName: documentSourceName },
        select: { id: true },
      });
      const documentId = createdDocumentId ?? createdDocument?.id ?? null;
      const createdPdfDocument = await prisma.maxCourseDocument.findFirst({
        where: { organizationId, courseId, sourceName: pdfSourceName },
        select: { id: true },
      });
      const pdfDocumentId = createdPdfDocumentId ?? createdPdfDocument?.id ?? null;
      await prisma.auditLogEvent.deleteMany({ where: {
        actorId: actors[0].id,
        OR: [{ objectId: courseId, action: "courses:assign" },
          ...(employeeId ? [{ objectId: employeeId }] : []),
          ...(documentId ? [{ objectId: documentId }] : []),
          ...(pdfDocumentId ? [{ objectId: pdfDocumentId }] : [])],
      } });
      if (documentId) await prisma.maxCourseDocument.delete({ where: { id: documentId } });
      if (pdfDocumentId) await prisma.maxCourseDocument.delete({ where: { id: pdfDocumentId } });
      await prisma.user.deleteMany({ where: { id: { in: [...actors.map(({ id }) => id), probeLearnerId,
        ...(employeeId ? [employeeId] : [])] } } });
    } finally {
      await prisma.$disconnect();
    }
  }
}

run().catch(() => {
  console.error("MAX manager pilot check or cleanup failed; no credentials were logged.");
  process.exitCode = 1;
});
