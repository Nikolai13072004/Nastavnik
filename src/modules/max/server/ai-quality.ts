import "server-only";

import prisma from "@/lib/prisma";
import type { MaxLearnerIdentity } from "../application/list-courses";
import type { KnowledgeAnswer } from "../application/verify-knowledge-answer";
import { knowledgeFeedbackHash } from "../infrastructure/knowledge-feedback-proof";
import { hasCourseAccess } from "./course-learning";
import { requireMaxReportManager } from "./manager-learning-history";

export async function recordMaxAiEvent(
  identity: MaxLearnerIdentity,
  courseId: string,
  durationMs: number,
  question: string,
  result: KnowledgeAnswer | null,
) {
  const event = await prisma.maxAiEvent.create({
    data: {
      organizationId: identity.organizationId,
      courseId,
      userId: identity.userId,
      outcome: result ? (result.refused ? "REFUSED" : "ANSWERED") : "ERROR",
      durationMs: Math.min(2147483647, Math.max(0, Math.round(durationMs))),
      sourceCount: result?.sources.length ?? 0,
      payloadHash: result ? knowledgeFeedbackHash(question, result) : null,
    },
    select: { id: true },
  });
  return event.id;
}

export async function submitMaxAiFeedback(
  identity: MaxLearnerIdentity,
  input: {
    courseId: string;
    eventId: string;
    question: string;
    result: KnowledgeAnswer;
    reason: string;
    comment: string;
  },
) {
  if ((await hasCourseAccess(identity, input.courseId)) !== "ALLOWED")
    return { error: "FORBIDDEN" } as const;
  const event = await prisma.maxAiEvent.findFirst({
    where: {
      id: input.eventId,
      userId: identity.userId,
      courseId: input.courseId,
      organizationId: identity.organizationId,
      createdAt: { gte: new Date(Date.now() - 7 * 86400000) },
    },
  });
  if (
    !event ||
    !event.payloadHash ||
    event.payloadHash !== knowledgeFeedbackHash(input.question, input.result)
  ) {
    return { error: "NOT_FOUND" } as const;
  }
  const saved = await prisma.maxAiFeedback.createMany({
    data: [
      {
        eventId: event.id,
        question: input.question,
        answer: input.result.answer,
        sourcesJson: JSON.stringify(
          input.result.sources.map((source) => ({
            documentId: source.documentId,
            documentHash: source.documentHash,
            title: source.title,
            section: source.section,
            snippet: source.snippet,
            pageStart: source.pageStart,
            pageEnd: source.pageEnd,
            courseDocumentId: source.courseDocumentId ?? null,
          })),
        ),
        reason: input.reason,
        comment: input.comment || null,
      },
    ],
    skipDuplicates: true,
  });
  return { status: saved.count ? "SAVED" : "ALREADY_REPORTED" } as const;
}

export async function getMaxAiQuality(
  identity: MaxLearnerIdentity,
  courseId: string,
) {
  const manager = await requireMaxReportManager(identity);
  if (!manager) return { error: "FORBIDDEN" } as const;
  if (
    !(await prisma.course.findFirst({
      where: { id: courseId, organizationId: identity.organizationId },
    }))
  ) {
    return { error: "NOT_FOUND" } as const;
  }
  const where = {
    courseId,
    organizationId: identity.organizationId,
    createdAt: { gte: new Date(Date.now() - 30 * 86400000) },
  };
  const [outcomes, timings, feedbackCount, verdicts, feedback] =
    await Promise.all([
      prisma.maxAiEvent.groupBy({
        by: ["outcome"],
        where,
        _count: { _all: true },
        _avg: { durationMs: true },
      }),
      prisma.maxAiEvent.findMany({
        where,
        orderBy: [{ createdAt: "desc" }, { id: "desc" }],
        take: 1000,
        select: { durationMs: true },
      }),
      prisma.maxAiFeedback.count({ where: { event: where } }),
      prisma.maxAiFeedback.groupBy({
        by: ["verdict"],
        where: { event: where },
        _count: { _all: true },
      }),
      prisma.maxAiFeedback.findMany({
        where: { event: where },
        orderBy: [{ createdAt: "desc" }, { eventId: "desc" }],
        take: 51,
        select: {
          eventId: true,
          question: true,
          answer: true,
          sourcesJson: true,
          reason: true,
          comment: true,
          verdict: true,
          reviewComment: true,
          reviewedAt: true,
          createdAt: true,
        },
      }),
    ]);
  const total = outcomes.reduce((sum, row) => sum + row._count._all, 0);
  const times = timings
    .map(({ durationMs }) => durationMs)
    .sort((a, b) => a - b);
  return {
    periodDays: 30,
    total,
    answered:
      outcomes.find(({ outcome }) => outcome === "ANSWERED")?._count._all ?? 0,
    refused:
      outcomes.find(({ outcome }) => outcome === "REFUSED")?._count._all ?? 0,
    errors:
      outcomes.find(({ outcome }) => outcome === "ERROR")?._count._all ?? 0,
    averageMs: total
      ? Math.round(
          outcomes.reduce(
            (sum, row) => sum + (row._avg.durationMs ?? 0) * row._count._all,
            0,
          ) / total,
        )
      : null,
    p95Ms: times.length ? times[Math.ceil(times.length * 0.95) - 1] : null,
    timingSampleCount: times.length,
    feedbackCount,
    verdicts: verdicts.map(({ verdict, _count }) => ({
      verdict,
      count: _count._all,
    })),
    feedback: feedback.slice(0, 50),
    feedbackTruncated: feedback.length > 50,
  };
}

export async function reviewMaxAiFeedback(
  identity: MaxLearnerIdentity,
  courseId: string,
  eventId: string,
  verdict: "CORRECT" | "INCORRECT" | "INCONCLUSIVE",
  comment: string,
) {
  const manager = await requireMaxReportManager(identity);
  if (!manager) return { error: "FORBIDDEN" } as const;
  return prisma.$transaction(async (tx) => {
    if (!(await requireMaxReportManager(identity, tx)))
      return { error: "FORBIDDEN" } as const;
    const updated = await tx.maxAiFeedback.updateMany({
      where: {
        eventId,
        verdict: "PENDING",
        event: {
          courseId,
          organizationId: identity.organizationId,
          course: { organizationId: identity.organizationId },
        },
      },
      data: {
        verdict,
        reviewComment: comment || null,
        reviewedById: identity.userId,
        reviewedAt: new Date(),
      },
    });
    if (!updated.count) return { error: "NOT_FOUND_OR_REVIEWED" } as const;
    await tx.auditLogEvent.create({
      data: {
        actorId: identity.userId,
        action: "max_ai:review",
        objectType: "max_ai_feedback",
        objectId: eventId,
        metadataJson: JSON.stringify({ courseId, verdict }),
      },
    });
    return { status: "REVIEWED" } as const;
  });
}
