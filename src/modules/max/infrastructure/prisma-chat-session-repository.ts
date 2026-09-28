import prisma from "@/lib/prisma";
import type { ChatSessionRepository, ChatState } from "../application/chat";
import { createChatFeedbackCodec } from "./chat-feedback-codec";

export function createPrismaChatSessions(botUsername: string): ChatSessionRepository {
  return {
    async load(identity) {
      const session = await prisma.maxChatSession.findUnique({
        where: { botUsername_maxUserId: { botUsername, maxUserId: identity.maxUserId } },
      });
      if (!session || session.expiresAt <= new Date() || session.userId !== identity.userId ||
          session.organizationId !== identity.organizationId || session.linkedAt.toISOString() !== identity.linkedAt) return null;
      const state = JSON.parse(session.stateJson) as ChatState & { aiFeedbackCiphertext?: string };
      delete state.aiFeedback;
      if (state.aiFeedbackCiphertext && process.env.MAX_BOT_TOKEN) {
        try {
          state.aiFeedback = createChatFeedbackCodec(process.env.MAX_BOT_TOKEN)
            .open(state.aiFeedbackCiphertext, `${botUsername}:${identity.userId}:${identity.linkedAt}`);
        } catch {
          // A lost or rotated key drops feedback, not the saved quiz draft.
        }
      }
      delete state.aiFeedbackCiphertext;
      if (!/^[a-zA-Z0-9_-]{16}$/.test(state.version)) return null;
      return state;
    },
    async save(identity, state) {
      const expiresAt = new Date(Date.now() + 30 * 60_000);
      const { aiFeedback, ...stored } = state;
      const aiFeedbackCiphertext = aiFeedback && process.env.MAX_BOT_TOKEN
        ? createChatFeedbackCodec(process.env.MAX_BOT_TOKEN).seal(aiFeedback, `${botUsername}:${identity.userId}:${identity.linkedAt}`) : undefined;
      const data = { userId: identity.userId, organizationId: identity.organizationId,
        linkedAt: new Date(identity.linkedAt), stateJson: JSON.stringify({ ...stored, aiFeedbackCiphertext }), expiresAt };
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
