import assert from "node:assert/strict";
import { test } from "node:test";
import { USER_STATUSES } from "@/lib/users";
import { UserApplicationError } from "./errors";
import { createArchiveUser } from "./archive-user";
import type {
  UserLifecycleEffects,
  UserLifecycleRepository,
  UserLifecycleTransaction,
  UserRecord,
} from "./ports";

// Use-case архивирования. Проверяется через фейковый репозиторий (без Prisma):
// happy-path, self-block, not-found, already-archived, состав побочных эффектов.

function makeRepository(opts: { users?: UserRecord[] }) {
  const state = {
    statusChanges: [] as Array<{ userIds: string[]; nextStatus: string }>,
    cancelledInvites: [] as string[][],
    effects: [] as UserLifecycleEffects[],
  };

  const repository: UserLifecycleRepository = {
    async transact(execute) {
      const transaction: UserLifecycleTransaction = {
        async deleteUserEmailJobsByEmail() {
          assert.fail("Archiving or restoring must not delete email jobs");
        },
        async deleteCourseInvitesByEmail() {
          assert.fail("Archiving or restoring must not delete course invites");
        },
        async deleteCourseInvitesByAcceptedUserId() {
          assert.fail("Archiving or restoring must not delete accepted invites");
        },
        async hardDeleteUser() {
          assert.fail("Archiving or restoring must not permanently delete users");
        },
        async findUsersByIds(userIds) {
          return (opts.users ?? []).filter((user) => userIds.includes(user.id));
        },
        async changeUserStatuses(userIds, nextStatus) {
          state.statusChanges.push({ userIds: [...userIds], nextStatus });
        },
        async cancelPendingInvites(userIds) {
          state.cancelledInvites.push([...userIds]);
        },
        async recordEffects(effects) {
          state.effects.push(effects);
        },
      };
      return execute(transaction);
    },
  };

  return { repository, state };
}

const AUDIT = {
  actorId: "admin-1",
  actorLogin: "admin@corp.ru",
  actorName: "Админ",
  ipAddress: "10.0.0.1",
  userAgent: "test-agent",
};

const ACTIVE_USER: UserRecord = {
  id: "u1",
  login: "ivan",
  email: "ivan@corp.ru",
  name: "Иван Петров",
  status: USER_STATUSES.ACTIVE,
};

test("архивирование ACTIVE-пользователя: смена статуса, отмена приглашений и запись аудита", async () => {
  const { repository, state } = makeRepository({ users: [ACTIVE_USER] });
  const archiveUser = createArchiveUser({ repository });

  const result = await archiveUser({
    userId: "u1",
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.user.id, "u1");
  assert.equal(result.previousStatus, USER_STATUSES.ACTIVE);
  assert.deepEqual(state.statusChanges, [
    { userIds: ["u1"], nextStatus: USER_STATUSES.ARCHIVED },
  ]);
  assert.deepEqual(state.cancelledInvites, [["u1"]]);
  assert.equal(state.effects.length, 1);
  const audit = state.effects[0].audit!;
  assert.equal(audit.action, "users:archive");
  assert.equal(audit.objectId, "u1");
  assert.equal(audit.actorId, "admin-1");
  assert.equal(audit.ipAddress, "10.0.0.1");
  assert.deepEqual(audit.metadata, {
    login: "ivan",
    email: "ivan@corp.ru",
    previousStatus: USER_STATUSES.ACTIVE,
    nextStatus: USER_STATUSES.ARCHIVED,
  });
});

test("попытка архивировать самого себя → SELF_BLOCK без обращения к репозиторию", async () => {
  const { repository, state } = makeRepository({ users: [ACTIVE_USER] });
  const archiveUser = createArchiveUser({ repository });

  await assert.rejects(
    archiveUser({ userId: "u1", currentUserId: "u1", audit: AUDIT }),
    (error) =>
      error instanceof UserApplicationError && error.code === "SELF_BLOCK",
  );
  assert.equal(state.statusChanges.length, 0, "мутаций не было");
  assert.equal(state.effects.length, 0, "аудита не было");
});

test("пользователь не найден → NOT_FOUND, никаких мутаций", async () => {
  const { repository, state } = makeRepository({ users: [] });
  const archiveUser = createArchiveUser({ repository });

  await assert.rejects(
    archiveUser({ userId: "missing", currentUserId: "admin-1", audit: AUDIT }),
    (error) =>
      error instanceof UserApplicationError && error.code === "NOT_FOUND",
  );
  assert.equal(state.statusChanges.length, 0);
  assert.equal(state.cancelledInvites.length, 0);
  assert.equal(state.effects.length, 0);
});

test("пользователь уже архивирован → ALREADY_ARCHIVED, повторных мутаций нет", async () => {
  const archived: UserRecord = { ...ACTIVE_USER, status: USER_STATUSES.ARCHIVED };
  const { repository, state } = makeRepository({ users: [archived] });
  const archiveUser = createArchiveUser({ repository });

  await assert.rejects(
    archiveUser({ userId: "u1", currentUserId: "admin-1", audit: AUDIT }),
    (error) =>
      error instanceof UserApplicationError &&
      error.code === "ALREADY_ARCHIVED",
  );
  assert.equal(state.statusChanges.length, 0);
  assert.equal(state.effects.length, 0);
});
