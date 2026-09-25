import { buildUserDisplayName } from "@/lib/users";

export type MaxEmployeeIdentity = {
  maxUserId: string;
  userId: string;
  organizationId: string;
  linkedAt: string;
};

export type MaxEmployeeInput = {
  firstName: string;
  lastName: string;
  email: string;
};

export type MaxEmployeeActor = {
  id: string;
  login: string;
  name: string;
  organizationId: string;
};

export type MaxEmployeeTransaction = {
  findActor(identity: MaxEmployeeIdentity): Promise<MaxEmployeeActor | null>;
  createEmployee(input: MaxEmployeeInput & {
    name: string;
    organizationId: string;
    passwordHash: string;
  }): Promise<{ id: string; name: string }>;
  issueLinkCode(userId: string, organizationId: string, tokenHash: string, now: Date, expiresAt: Date): Promise<void>;
  recordAudit(actor: MaxEmployeeActor, employee: { id: string; name: string }, email: string): Promise<void>;
  findEmployee(userId: string, organizationId: string): Promise<{ id: string; name: string } | null>;
  upsertLinkCode(userId: string, organizationId: string, tokenHash: string, now: Date, expiresAt: Date): Promise<void>;
  recordCodeAudit(actor: MaxEmployeeActor, employee: { id: string; name: string }): Promise<void>;
};

export type MaxEmployeeRepository = {
  transact<T>(work: (transaction: MaxEmployeeTransaction) => Promise<T>): Promise<T>;
  isUniqueViolation(error: unknown): boolean;
};

export function createCreateMaxEmployee(deps: {
  repository: MaxEmployeeRepository;
  createPasswordHash: () => Promise<string>;
  createToken: () => string;
  hashToken: (token: string) => string;
  now: () => Date;
}) {
  return async function createMaxEmployee(identity: MaxEmployeeIdentity, input: MaxEmployeeInput) {
    const firstName = input.firstName.trim();
    const lastName = input.lastName.trim();
    const email = input.email.trim().toLowerCase();
    if (!firstName || firstName.length > 80 || lastName.length > 80 ||
        !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
      return { status: "INVALID_INPUT" as const };
    }

    try {
      return await deps.repository.transact(async (transaction) => {
        const actor = await transaction.findActor(identity);
        if (!actor) return { status: "FORBIDDEN" as const };

        const passwordHash = await deps.createPasswordHash();
        const token = deps.createToken();
        const now = deps.now();
        const expiresAt = new Date(now.getTime() + 15 * 60_000);
        const employee = await transaction.createEmployee({
          firstName,
          lastName,
          email,
          name: buildUserDisplayName(firstName, lastName),
          organizationId: actor.organizationId,
          passwordHash,
        });
        await transaction.issueLinkCode(employee.id, actor.organizationId, deps.hashToken(token), now, expiresAt);
        await transaction.recordAudit(actor, employee, email);
        return { status: "CREATED" as const, employee, token, expiresAt: expiresAt.toISOString() };
      });
    } catch (error) {
      if (deps.repository.isUniqueViolation(error)) return { status: "EMAIL_EXISTS" as const };
      throw error;
    }
  };
}
