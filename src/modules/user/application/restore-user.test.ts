import assert from "node:assert/strict";
import { test } from "node:test";
import { USER_STATUSES } from "@/lib/users";
import { UserApplicationError } from "./errors";
import { createRestoreUser } from "./restore-user";
import type {
  UserLifecycleEffects,
  UserLifecycleRepository,
  UserLifecycleTransaction,
  UserRecord,
} from "./ports";

// Use-case восстановления пользователя (ARCHIVED → ACTIVE).
// Проверяется через фейковый репозиторий: happy-path, not-found и
// защита от восстановления неархивированных.

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
  ipAddress: null,
  userAgent: null,
};

const ARCHIVED_USER: UserRecord = {
  id: "u1",
  login: "ivan",
  email: "ivan@corp.ru",
  name: "Иван Петров",
  status: USER_STATUSES.ARCHIVED,
};

test("архивированный пользователь восстановлен: смена статуса, аудит, приглашения не трогаем", async () => {
  const { repository, state } = makeRepository({ users: [ARCHIVED_USER] });
  const restoreUser = createRestoreUser({ repository });

  const result = await restoreUser({ userId: "u1", audit: AUDIT });

  assert.equal(result.previousStatus, USER_STATUSES.ARCHIVED);
  assert.deepEqual(state.statusChanges, [
    { userIds: ["u1"], nextStatus: USER_STATUSES.ACTIVE },
  ]);
  assert.equal(
    state.cancelledInvites.length,
    0,
    "восстановление не трогает приглашения",
  );
  assert.equal(state.effects.length, 1);
  const audit = state.effects[0].audit!;
  assert.equal(audit.action, "users:restore");
  assert.equal(audit.objectId, "u1");
  assert.deepEqual(audit.metadata, {
    login: "ivan",
    email: "ivan@corp.ru",
    previousStatus: USER_STATUSES.ARCHIVED,
    nextStatus: USER_STATUSES.ACTIVE,
  });
});

test("пользователь не найден → NOT_FOUND", async () => {
  const { repository, state } = makeRepository({ users: [] });
  const restoreUser = createRestoreUser({ repository });

  await assert.rejects(
    restoreUser({ userId: "missing", audit: AUDIT }),
    (error) =>
      error instanceof UserApplicationError && error.code === "NOT_FOUND",
  );
  assert.equal(state.statusChanges.length, 0);
  assert.equal(state.effects.length, 0);
});

test("нельзя восстановить неархивированного пользователя → NOT_ARCHIVED", async () => {
  const active: UserRecord = { ...ARCHIVED_USER, status: USER_STATUSES.ACTIVE };
  const { repository, state } = makeRepository({ users: [active] });
  const restoreUser = createRestoreUser({ repository });

  await assert.rejects(
    restoreUser({ userId: "u1", audit: AUDIT }),
    (error) =>
      error instanceof UserApplicationError && error.code === "NOT_ARCHIVED",
  );
  assert.equal(state.statusChanges.length, 0);
  assert.equal(state.effects.length, 0);
});
