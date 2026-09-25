import "server-only";

import { createHash, randomBytes } from "node:crypto";
import { createMaxAccountLinking } from "../application/link-account";
import { prismaMaxLinkRepository } from "../infrastructure/prisma-max-link-repository";

export const maxAccountLinking = createMaxAccountLinking({
  repository: prismaMaxLinkRepository,
  createToken: () => randomBytes(24).toString("base64url"),
  hashToken: (token) => createHash("sha256").update(token).digest("hex"),
  now: () => new Date(),
});
