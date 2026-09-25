import "server-only";

import { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { hasCourseAccess } from "./course-learning";
import { ensureCurrentMaxDocumentTraining } from "../infrastructure/prisma-max-course-documents";

export async function recordMaxDocumentTraining(
  identity: MaxLearnerIdentity,
  courseId: string,
  documentId: string,
  action: "view" | "answer",
  answerIndex?: number,
) {
  const access = await hasCourseAccess(identity, courseId);
  if (access !== "ALLOWED") return { status: access } as const;
  await ensureCurrentMaxDocumentTraining(identity.organizationId, courseId, identity.userId);

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await prisma.$transaction(async (client) => {
        const document = await client.maxCourseDocument.findFirst({
          where: {
            id: documentId,
            organizationId: identity.organizationId,
            courseId,
            approvedAt: { not: null },
            revokedAt: null,
          },
          select: { checkCorrectIndex: true },
        });
        if (!document || document.checkCorrectIndex === null) return { status: "NOT_FOUND" } as const;

        const training = await client.maxDocumentTraining.findUnique({
          where: { documentId_userId: { documentId, userId: identity.userId } },
          select: { id: true, organizationId: true, courseId: true, viewedAt: true, passedAt: true, attempts: true },
        });
        if (!training || training.organizationId !== identity.organizationId || training.courseId !== courseId) {
          return { status: "NOT_ASSIGNED" } as const;
        }
        if (action === "view") {
          if (!training.viewedAt) {
            await client.maxDocumentTraining.update({
              where: { id: training.id }, data: { viewedAt: new Date() },
            });
          }
          return { status: "VIEWED" } as const;
        }
        if (!training.viewedAt) return { status: "READ_FIRST" } as const;
        if (training.passedAt) return { status: "PASSED", attempts: training.attempts } as const;
        if (training.attempts >= 3) return { status: "EXHAUSTED", attempts: training.attempts } as const;

        const correct = answerIndex === document.checkCorrectIndex;
        const updated = await client.maxDocumentTraining.update({
          where: { id: training.id },
          data: {
            attempts: { increment: 1 },
            lastAnsweredAt: new Date(),
            passedAt: correct ? new Date() : null,
          },
          select: { attempts: true },
        });
        return {
          status: correct ? "PASSED" : updated.attempts >= 3 ? "EXHAUSTED" : "INCORRECT",
          attempts: updated.attempts,
        } as const;
      }, { isolationLevel: Prisma.TransactionIsolationLevel.Serializable });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2034" && attempt < 2) {
        continue;
      }
      throw error;
    }
  }
  throw new Error("Could not record document training");
}
