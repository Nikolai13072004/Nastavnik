import "server-only";

import { createHash, randomBytes } from "node:crypto";
import bcrypt from "bcryptjs";
import { generatePasswordForPolicy, getPlatformSecuritySettings } from "@/lib/platform-settings";
import { createCreateMaxEmployee } from "../application/create-max-employee";
import { createIssueMaxEmployeeCode } from "../application/issue-max-employee-code";
import { prismaMaxEmployeeRepository } from "../infrastructure/prisma-max-employee-repository";

export const createMaxEmployee = createCreateMaxEmployee({
  repository: prismaMaxEmployeeRepository,
  createPasswordHash: async () => {
    const settings = await getPlatformSecuritySettings();
    return bcrypt.hash(generatePasswordForPolicy(settings), 10);
  },
  createToken: () => randomBytes(24).toString("base64url"),
  hashToken: (token) => createHash("sha256").update(token).digest("hex"),
  now: () => new Date(),
});

export const issueMaxEmployeeCode = createIssueMaxEmployeeCode({
  repository: prismaMaxEmployeeRepository,
  createToken: () => randomBytes(24).toString("base64url"),
  hashToken: (token) => createHash("sha256").update(token).digest("hex"),
  now: () => new Date(),
});
