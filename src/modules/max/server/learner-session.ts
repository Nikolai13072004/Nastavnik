import "server-only";

import { createMaxSessionCodec } from "../infrastructure/learner-session";
import { findMaxSessionIdentity } from "../infrastructure/prisma-max-learner-repository";

export async function issueMaxLearnerSession(maxUserId: string) {
  const identity = await findMaxSessionIdentity(maxUserId);
  if (!identity || !process.env.MAX_BOT_TOKEN) return null;
  return createMaxSessionCodec(process.env.MAX_BOT_TOKEN).issue(identity);
}
