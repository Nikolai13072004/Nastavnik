import type { MaxEmployeeIdentity, MaxEmployeeRepository } from "./create-max-employee";

export function createIssueMaxEmployeeCode(deps: {
  repository: MaxEmployeeRepository;
  createToken: () => string;
  hashToken: (token: string) => string;
  now: () => Date;
}) {
  return async function issueMaxEmployeeCode(identity: MaxEmployeeIdentity, userId: string) {
    if (!/^[\w-]{1,128}$/.test(userId)) return { status: "INVALID_INPUT" as const };

    return deps.repository.transact(async (transaction) => {
      const actor = await transaction.findActor(identity);
      if (!actor) return { status: "FORBIDDEN" as const };

      const employee = await transaction.findEmployee(userId, actor.organizationId);
      if (!employee) return { status: "NOT_FOUND" as const };

      const token = deps.createToken();
      const now = deps.now();
      const expiresAt = new Date(now.getTime() + 15 * 60_000);
      await transaction.upsertLinkCode(employee.id, actor.organizationId, deps.hashToken(token), now, expiresAt);
      await transaction.recordCodeAudit(actor, employee);
      return { status: "ISSUED" as const, token, expiresAt: expiresAt.toISOString() };
    });
  };
}
