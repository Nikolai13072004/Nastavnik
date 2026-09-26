import prisma from "@/lib/prisma";
import type { ChatSessionRepository, ChatState } from "../application/chat";

export function createPrismaChatSessions(botUsername: string): ChatSessionRepository {
  return {
    async load(identity) {
      const session = await prisma.maxChatSession.findUnique({
        where: { botUsername_maxUserId: { botUsername, maxUserId: identity.maxUserId } },
      });
      if (!session || session.expiresAt <= new Date() || session.userId !== identity.userId ||
          session.organizationId !== identity.organizationId || session.linkedAt.toISOString() !== identity.linkedAt) return null;
      const state = JSON.parse(session.stateJson) as ChatState;
      if (!/^[a-zA-Z0-9_-]{16}$/.test(state.version)) return null;
      return state;
    },
    async save(identity, state) {
      const expiresAt = new Date(Date.now() + 30 * 60_000);
      const data = { userId: identity.userId, organizationId: identity.organizationId,
        linkedAt: new Date(identity.linkedAt), stateJson: JSON.stringify(state), expiresAt };
      await prisma.maxChatSession.upsert({
        where: { botUsername_maxUserId: { botUsername, maxUserId: identity.maxUserId } },
        create: { botUsername, maxUserId: identity.maxUserId, ...data },
        update: data,
      });
    },
    async clear(maxUserId) {
      await prisma.maxChatSession.deleteMany({ where: { botUsername, maxUserId } });
    },
  };
}
