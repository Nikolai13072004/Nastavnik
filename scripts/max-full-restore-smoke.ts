import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { PrismaClient } from "@prisma/client";
import { storage } from "../src/lib/storage";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";

let check = "isolated environment";

async function main() {
  assert.equal(process.env.MAX_JOINT_REVIEW, "true");
  const database = new URL(process.env.DATABASE_URL ?? "");
  assert.equal(database.hostname, "db");
  assert.equal(database.pathname, "/max_review");
  assert.equal(process.env.MAX_DISABLE_EMAIL, "true");
  assert.ok(process.env.MAX_BOT_TOKEN?.startsWith("local-review-"));
  assert.equal(process.env.MAX_VEDOMO_ORIGIN, "https://joint-gateway:8443");
  const db = new PrismaClient();
  const origin = "https://joint-gateway:8443";
  try {
    check = "HTTPS health and missing session";
    assert.equal((await fetch("http://web:3000/api/health")).status, 200);
    assert.equal((await fetch(`${origin}/max`)).status, 200);
    assert.equal((await fetch(`${origin}/api/max/courses`)).status, 401);
    const originals = await db.maxCourseDocument.findMany({ where: { originalKey: { not: null } } });
    check = "original file bytes";
    for (const document of originals) {
      assert.ok(document.originalKey && document.originalHash && document.originalSize);
      const bytes = await storage.get("uploads", document.originalKey);
      assert.equal(bytes.length, document.originalSize);
      assert.equal(createHash("sha256").update(bytes).digest("hex"), document.originalHash);
    }
    const currentCourse = await db.course.findUniqueOrThrow({ where: { id: "max-pilot-onboarding-course" } });
    assert.ok(currentCourse.publishedSnapshotJson);
    const snapshotHash = createHash("sha256").update(currentCourse.publishedSnapshotJson).digest("hex");
    const mappings = await db.maxKnowledgeDocument.findMany({ where: {
      organizationId: "max-pilot-demo-org", courseId: currentCourse.id,
      publishedSnapshotHash: snapshotHash, revokedAt: null,
      courseDocument: { is: { approvedAt: { not: null }, revokedAt: null } },
    } });
    check = "restored source mappings";
    assert.ok(mappings.length > 0);
    for (const mapping of mappings) {
      const response = await fetch(`${origin}/api/integrations/prodigy/documents/${mapping.vedomoDocumentId}`, {
        headers: {
          Authorization: `Bearer ${process.env.MAX_VEDOMO_SERVICE_TOKEN}`,
          "X-Prodigy-Organization-Id": mapping.organizationId,
          "X-Prodigy-Course-Id": mapping.courseId,
        },
        signal: AbortSignal.timeout(15_000),
      });
      assert.equal(response.status, 200);
      const document = await response.json();
      assert.equal(document.document_id, mapping.vedomoDocumentId);
      assert.equal(document.document_hash, mapping.vedomoDocumentHash);
    }
    // Issue only a local session for a restored link. Do not change learner history.
    const link = await db.maxAccountLink.findFirstOrThrow({
      where: { organizationId: "max-pilot-demo-org", user: { role: "USER", status: "ACTIVE" } },
    });
    const token = createMaxSessionCodec(process.env.MAX_BOT_TOKEN!).issue({
      maxUserId: link.maxUserId, userId: link.userId,
      organizationId: link.organizationId, linkedAt: link.createdAt.toISOString(),
    }).token;
    const headers = { Authorization: `Bearer ${token}` };
    check = "learner course list";
    const courses = await fetch(`${origin}/api/max/courses`, { headers });
    assert.equal(courses.status, 200);
    const result = await courses.json();
    assert.ok(result.courses.some((course: { id: string }) => course.id === "max-pilot-onboarding-course"));
    const course = await fetch(`${origin}/api/max/course?courseId=max-pilot-onboarding-course`, { headers });
    check = "learner course contents";
    assert.equal(course.status, 200);
    const learning = await course.json();
    assert.ok(learning.course.materials.length > 0 && learning.course.quizzes.length > 0);
    console.log(`RESTORED_PRODIGY: ${originals.length} originals, ${mappings.length} source mappings and learner API verified.`);
  } finally {
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error(`RESTORED_PRODIGY_FAILED (${check}); credentials and learner details not printed.`);
  process.exitCode = 1;
});
