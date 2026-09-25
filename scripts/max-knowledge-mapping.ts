import { PrismaClient } from "@prisma/client";
import {
  approveMaxKnowledgeDocument,
  revokeMaxKnowledgeDocument,
} from "../src/modules/max/infrastructure/approve-max-knowledge-document";
import { createVedomoClient } from "../src/modules/max/infrastructure/vedomo-client";

function option(name: string) {
  const index = process.argv.indexOf(`--${name}`);
  const value = process.argv[index + 1];
  if (index < 0 || !value || value.startsWith("--")) {
    throw new Error(`Missing --${name}`);
  }
  return value;
}

async function main() {
  const operation = process.argv[2];
  const db = new PrismaClient();
  try {
    let result;
    if (operation === "approve") {
      const courseId = option("course-id");
      const courseDocumentId = option("course-document-id");
      const vedomoDocumentId = option("vedomo-document-id");
      const vedomoDocumentHash = option("vedomo-document-hash");
      const course = await db.course.findUnique({
        where: { id: courseId },
        select: { organizationId: true },
      });
      if (!course?.organizationId) throw new Error("Course not found");
      const document = await createVedomoClient(
        process.env.MAX_VEDOMO_ORIGIN ?? "",
        process.env.MAX_VEDOMO_SERVICE_TOKEN ?? "",
      ).getDocument(course.organizationId, courseId, vedomoDocumentId);
      if (document.documentHash !== vedomoDocumentHash) {
        throw new Error("Vedomo document hash does not match");
      }
      result = await approveMaxKnowledgeDocument(db, {
        courseId,
        courseDocumentId,
        vedomoDocumentId,
        vedomoDocumentHash,
      });
    } else if (operation === "revoke") {
      result = await revokeMaxKnowledgeDocument(
        db,
        option("course-id"),
        option("vedomo-document-id"),
      );
    } else {
      throw new Error("Use approve or revoke");
    }
    process.stdout.write(`${result}\n`);
    if (result !== "APPROVED" && result !== "REVOKED") process.exitCode = 1;
  } finally {
    await db.$disconnect();
  }
}

main().catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.message : "Mapping failed"}\n`);
  process.exitCode = 1;
});
