import "server-only";

import { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { MaxLinkError, type MaxLinkRepository } from "../application/link-account";

async function transact<T>(operation: (tx: Prisma.TransactionClient) => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await prisma.$transaction(operation, { isolationLevel: "Serializable" });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError) {
        if (error.code === "P2034" && attempt < 2) continue;
        if (error.code === "P2002") throw new MaxLinkError("ALREADY_LINKED");
      }
      throw error;
    }
  }
}

export const prismaMaxLinkRepository: MaxLinkRepository = {
  async issue({ userId, tokenHash, expiresAt, now }) {
    await transact(async (tx) => {
      const user = await tx.user.findUnique({
        where: { id: userId },
        select: { status: true, organizationId: true, maxLink: { select: { maxUserId: true } } },
      });
      if (!user || user.status !== "ACTIVE" || !user.organizationId) throw new MaxLinkError("UNAVAILABLE");
      if (user.maxLink) throw new MaxLinkError("ALREADY_LINKED");
      const data = { tokenHash, expiresAt, createdAt: now, organizationId: user.organizationId };
      await tx.maxLinkInvite.upsert({ where: { userId }, create: { userId, ...data }, update: data });
    });
  },
  async accept({ tokenHash, maxUserId, now }) {
    await transact(async (tx) => {
      const invite = await tx.maxLinkInvite.findUnique({
        where: { tokenHash },
        include: { user: { select: { status: true, organizationId: true } } },
      });
      if (!invite || invite.expiresAt <= now || invite.user.status !== "ACTIVE" ||
          invite.organizationId !== invite.user.organizationId) {
        throw new MaxLinkError("INVALID_INVITE");
      }
      const existing = await tx.maxAccountLink.findFirst({
        where: { OR: [{ maxUserId }, { userId: invite.userId }] },
        select: { maxUserId: true },
      });
      if (existing) throw new MaxLinkError("ALREADY_LINKED");
      const consumed = await tx.maxLinkInvite.deleteMany({ where: { tokenHash, expiresAt: { gt: now } } });
      if (consumed.count !== 1) throw new MaxLinkError("INVALID_INVITE");
      await tx.maxAccountLink.create({ data: { maxUserId, userId: invite.userId, organizationId: invite.organizationId } });
    });
  },
  async findEmployee(maxUserId) {
    const link = await prisma.maxAccountLink.findUnique({
      where: { maxUserId },
      select: {
        organizationId: true,
        user: { select: { name: true, status: true, organizationId: true, organization: { select: { name: true } } } },
      },
    });
    // Re-evaluate on every request, never trust the role/org supplied by MAX.
    if (!link || link.user.status !== "ACTIVE" || link.user.organizationId !== link.organizationId || !link.user.organization) return null;
    return { name: link.user.name, organizationName: link.user.organization.name };
  },
};
