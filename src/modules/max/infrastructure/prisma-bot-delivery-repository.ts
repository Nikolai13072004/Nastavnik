import { createHash } from "node:crypto";
import prisma from "@/lib/prisma";
import type { BotDeliveryRepository } from "../application/bot-delivery";
import { resolveEnrollmentAccess } from "@/modules/enrollment/domain/enrollment-access";

export const prismaBotDeliveryRepository: BotDeliveryRepository = {
  async enqueue(botUsername, event) {
    const help = "kind" in event && event.kind === "HELP";
    const identity = help
      ? [botUsername, "help", event.userId, Math.floor(event.timestamp / 30_000)]
      : [botUsername, "bot_started", event.userId, "chatId" in event ? event.chatId : null, event.timestamp];
    const eventKey = createHash("sha256")
      .update(JSON.stringify(identity))
      .digest("hex");
    // Insert-on-conflict ignores exact redeliveries, including after a process restart.
    // Do not store names, message text or deep-link payload (it may contain a secret).
    await prisma.maxBotDelivery.createMany({
      // Generic help is limited to one reply per user in each 30-second window.
      data: [{ eventKey, botUsername, maxUserId: String(event.userId), kind: help ? "HELP" : "WELCOME" }],
      skipDuplicates: true,
    });
  },

  async claim(botUsername) {
    return prisma.$transaction(async (tx) => {
      // A DB-scoped lock coordinates every worker, not just this Node process.
      const [lock] = await tx.$queryRaw<Array<{ acquired: boolean }>>`
        SELECT pg_try_advisory_xact_lock(7123, hashtext(${botUsername})) AS acquired
      `;
      if (!lock.acquired) return null;
      const inFlight = await tx.maxBotDelivery.findFirst({ where: { botUsername, status: "SENDING" } });
      if (inFlight) return null;
      const latest = await tx.maxBotDelivery.findFirst({
        where: { botUsername, finishedAt: { not: null } },
        orderBy: { finishedAt: "desc" },
      });
      const [clock] = await tx.$queryRaw<Array<{ now: Date }>>`SELECT clock_timestamp() AS now`;
      if (latest?.finishedAt && clock.now.getTime() - latest.finishedAt.getTime() < 1100) return null;
      const job = await tx.maxBotDelivery.findFirst({
        where: { botUsername, status: "PENDING" },
        orderBy: [{ createdAt: "asc" }, { eventKey: "asc" }],
      });
      if (!job) return null;
      if (job.kind !== "WELCOME" && job.kind !== "DOCUMENT_REVISION" && job.kind !== "HELP") {
        throw new Error("Unknown MAX delivery kind");
      }
      if (job.kind === "DOCUMENT_REVISION") {
        const training = job.documentId ? await tx.maxDocumentTraining.findFirst({
          where: {
            documentId: job.documentId,
            document: { approvedAt: { not: null }, revokedAt: null },
            user: {
              status: "ACTIVE",
              maxLink: { is: { maxUserId: job.maxUserId } },
            },
          },
          select: { userId: true, organizationId: true, courseId: true },
        }) : null;
        const link = training ? await tx.maxAccountLink.findFirst({
          where: { maxUserId: job.maxUserId, userId: training.userId,
            organizationId: training.organizationId },
          select: { userId: true },
        }) : null;
        const learner = training ? await tx.user.findFirst({
          where: { id: training.userId, organizationId: training.organizationId, status: "ACTIVE" },
          select: {
            directCourseAssignments: {
              where: { courseId: training.courseId }, select: { expiresAt: true },
            },
            groupMemberships: { select: { group: { select: {
              courseAssignments: {
                where: { courseId: training.courseId }, select: { expiresAt: true },
              },
            } } } },
          },
        }) : null;
        const access = learner ? resolveEnrollmentAccess({
          directExpiries: learner.directCourseAssignments.map(({ expiresAt }) => expiresAt),
          groupExpiries: learner.groupMemberships.flatMap(({ group }) =>
            group.courseAssignments.map(({ expiresAt }) => expiresAt)),
          now: clock.now,
        }).isActive : false;
        if (!training || !link || !access) {
          await tx.maxBotDelivery.update({
            where: { eventKey: job.eventKey },
            data: { status: "SKIPPED", finishedAt: clock.now },
          });
          return null;
        }
      }
      await tx.maxBotDelivery.update({
        where: { eventKey: job.eventKey },
        data: { status: "SENDING", startedAt: clock.now },
      });
      return { eventKey: job.eventKey, maxUserId: job.maxUserId,
        kind: job.kind, documentId: job.documentId };
    });
  },

  async finish(eventKey, outcome) {
    const [clock] = await prisma.$queryRaw<Array<{ now: Date }>>`SELECT clock_timestamp() AS now`;
    const result = await prisma.maxBotDelivery.updateMany({
      where: { eventKey, status: "SENDING" },
      data: {
        status: outcome.status,
        finishedAt: clock.now,
        messageId: outcome.status === "SENT" ? outcome.messageId : null,
        errorCode: outcome.status === "UNCERTAIN" ? outcome.errorCode : null,
      },
    });
    if (result.count !== 1) throw new Error("MAX delivery state conflict");
  },
};
