export class MaxLinkError extends Error {
  constructor(public readonly code: "UNAVAILABLE" | "INVALID_INVITE" | "ALREADY_LINKED") {
    super(code);
  }
}

export type LinkedEmployee = { name: string; organizationName: string };

export interface MaxLinkRepository {
  issue(input: { userId: string; tokenHash: string; expiresAt: Date; now: Date }): Promise<void>;
  // Implementations must atomically consume the invite and create the link.
  accept(input: { tokenHash: string; maxUserId: string; now: Date }): Promise<void>;
  findEmployee(maxUserId: string): Promise<LinkedEmployee | null>;
}

export function createMaxAccountLinking(deps: {
  repository: MaxLinkRepository;
  createToken: () => string;
  hashToken: (token: string) => string;
  now: () => Date;
}) {
  return {
    async issue(userId: string) {
      const token = deps.createToken();
      const now = deps.now();
      const expiresAt = new Date(now.getTime() + 15 * 60_000);
      await deps.repository.issue({ userId, tokenHash: deps.hashToken(token), now, expiresAt });
      return { token, expiresAt: expiresAt.toISOString() };
    },
    async accept(token: string, maxUserId: string) {
      if (!/^[a-zA-Z0-9_-]{32}$/.test(token)) throw new MaxLinkError("INVALID_INVITE");
      await deps.repository.accept({ tokenHash: deps.hashToken(token), maxUserId, now: deps.now() });
    },
    findEmployee: (maxUserId: string) => deps.repository.findEmployee(maxUserId),
  };
}
