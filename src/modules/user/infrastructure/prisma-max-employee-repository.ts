import "server-only";

import { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { getPermissionsForRoleNames } from "@/lib/role-profiles";
import { PERMISSIONS, ROLE_PERMISSIONS, STANDARD_ROLE_NAMES } from "@/lib/roles";
import type { MaxEmployeeRepository, MaxEmployeeTransaction } from "../application/create-max-employee";

function createTransaction(client: Prisma.TransactionClient): MaxEmployeeTransaction {
  return {
    async findActor(identity) {
      const link = await client.maxAccountLink.findUnique({
        where: { maxUserId: identity.maxUserId },
        select: {
          userId: true,
          organizationId: true,
          createdAt: true,
          user: {
            select: {
              id: true,
              login: true,
              name: true,
              role: true,
              status: true,
              organizationId: true,
              userRoles: { select: { roleProfile: { select: { name: true } } } },
            },
          },
        },
      });
      if (!link || link.userId !== identity.userId ||
          link.organizationId !== identity.organizationId ||
          link.createdAt.toISOString() !== identity.linkedAt ||
          link.user.status !== "ACTIVE" ||
          link.user.organizationId !== identity.organizationId) {
        return null;
      }

      const roles = [link.user.role, ...link.user.userRoles.map(({ roleProfile }) => roleProfile.name)];
      const permissions = await getPermissionsForRoleNames(roles, client);
      if (!permissions.includes(PERMISSIONS.USERS_CREATE) ||
          !permissions.includes(PERMISSIONS.USERS_VIEW) ||
          !permissions.includes(PERMISSIONS.REPORTS_VIEW)) {
        return null;
      }
      return {
        id: link.user.id,
        login: link.user.login,
        name: link.user.name,
        organizationId: identity.organizationId,
      };
    },
    async createEmployee(input) {
      const studentRole = await client.roleProfile.upsert({
        where: { name: STANDARD_ROLE_NAMES.STUDENT },
        create: {
          name: STANDARD_ROLE_NAMES.STUDENT,
          isSystem: true,
          permissionsJson: JSON.stringify(ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.STUDENT]),
        },
        update: {},
        select: { id: true },
      });

      return client.user.create({
        data: {
          firstName: input.firstName,
          lastName: input.lastName || null,
          name: input.name,
          login: input.email,
          email: input.email,
          passwordHash: input.passwordHash,
          role: STANDARD_ROLE_NAMES.STUDENT,
          status: "ACTIVE",
          organizationId: input.organizationId,
          userRoles: { create: [{ roleProfileId: studentRole.id }] },
        },
        select: { id: true, name: true },
      });
    },
    async issueLinkCode(userId, organizationId, tokenHash, now, expiresAt) {
      await client.maxLinkInvite.create({
        data: { userId, organizationId, tokenHash, createdAt: now, expiresAt },
      });
    },
    async findEmployee(userId, organizationId) {
      return client.user.findFirst({
        where: {
          id: userId,
          organizationId,
          status: "ACTIVE",
          role: STANDARD_ROLE_NAMES.STUDENT,
          userRoles: { every: { roleProfile: { name: STANDARD_ROLE_NAMES.STUDENT } } },
          maxLink: null,
        },
        select: { id: true, name: true },
      });
    },
    async upsertLinkCode(userId, organizationId, tokenHash, now, expiresAt) {
      await client.maxLinkInvite.upsert({
        where: { userId },
        create: { userId, organizationId, tokenHash, createdAt: now, expiresAt },
        update: { organizationId, tokenHash, createdAt: now, expiresAt },
      });
    },
    async recordCodeAudit(actor, employee) {
      await client.auditLogEvent.create({
        data: {
          actorId: actor.id,
          actorLogin: actor.login,
          actorName: actor.name,
          action: "users:max_link_code_issue",
          objectType: "user",
          objectId: employee.id,
          objectLabel: employee.name,
          metadataJson: JSON.stringify({ source: "max_mini_app", organizationId: actor.organizationId }),
        },
      });
    },
    async recordAudit(actor, employee, email) {
      await client.auditLogEvent.create({
        data: {
          actorId: actor.id,
          actorLogin: actor.login,
          actorName: actor.name,
          action: "users:create",
          objectType: "user",
          objectId: employee.id,
          objectLabel: employee.name,
          metadataJson: JSON.stringify({ source: "max_mini_app", email, organizationId: actor.organizationId,
            roles: [STANDARD_ROLE_NAMES.STUDENT], inviteQueued: false }),
        },
      });
    },
  };
}

export const prismaMaxEmployeeRepository: MaxEmployeeRepository = {
  async transact(work) {
    for (let attempt = 0; ; attempt++) {
      try {
        return await prisma.$transaction((client) => work(createTransaction(client)), {
          isolationLevel: "Serializable",
        });
      } catch (error) {
        if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2034" && attempt < 2) {
          continue;
        }
        throw error;
      }
    }
  },
  isUniqueViolation(error) {
    return error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2002";
  },
};
