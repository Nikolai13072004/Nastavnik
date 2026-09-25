import assert from "node:assert/strict";
import { test } from "node:test";
import { USER_STATUSES } from "@/lib/users";
import { createBulkArchiveUsers } from "./bulk-archive-users";
import type {
  UserLifecycleEffects,
  UserLifecycleRepository,
  UserLifecycleTransaction,
  UserRecord,
} from "./ports";

// Use-case массового архивирования. Логика «кого архивируем» — доменная функция
// planBulkArchive, здесь проверяется её интеграция: применение решения в
// транзакции, форма ответа для транспорта и подрезка аудита до 100 записей.

function makeRepository(users: UserRecord[]) {
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
          return users.filter((user) => userIds.includes(user.id));
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

function makeUser(id: string, status: UserRecord["status"] = USER_STATUSES.ACTIVE): UserRecord {
  return {
    id,
    login: `login-${id}`,
    email: `${id}@corp.ru`,
    name: `User ${id}`,
    status,
  };
}

test("пустой запрос → NOTHING_TO_ARCHIVE, репозиторий не открывался", async () => {
  const { repository, state } = makeRepository([]);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: [],
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "NOTHING_TO_ARCHIVE");
  assert.equal(state.effects.length, 0);
});

test("несколько активных: архивируем всех, отменяем приглашения, пишем один аудит", async () => {
  const users = [makeUser("u1"), makeUser("u2"), makeUser("u3")];
  const { repository, state } = makeRepository(users);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: ["u1", "u2", "u3"],
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "ARCHIVED");
  if (result.status !== "ARCHIVED") return;
  assert.equal(result.archivedCount, 3);
  assert.equal(result.skippedCurrentUser, false);
  assert.deepEqual(state.statusChanges[0].userIds, ["u1", "u2", "u3"]);
  assert.equal(state.statusChanges[0].nextStatus, USER_STATUSES.ARCHIVED);
  assert.deepEqual(state.cancelledInvites[0], ["u1", "u2", "u3"]);
  assert.equal(state.effects.length, 1);
  assert.equal(state.effects[0].audit?.action, "users:bulk_archive");
});

test("текущий пользователь в выборке → пропущен, остальные архивированы", async () => {
  const users = [makeUser("u1"), makeUser("admin-1")];
  const { repository, state } = makeRepository(users);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: ["u1", "admin-1"],
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "ARCHIVED");
  if (result.status !== "ARCHIVED") return;
  assert.equal(result.archivedCount, 1);
  assert.equal(result.skippedCurrentUser, true);
  assert.deepEqual(state.statusChanges[0].userIds, ["u1"]);
});

test("только текущий пользователь в выборке → NOTHING_TO_ARCHIVE, skippedCurrentUser=true", async () => {
  const users = [makeUser("admin-1")];
  const { repository, state } = makeRepository(users);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: ["admin-1"],
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "NOTHING_TO_ARCHIVE");
  if (result.status !== "NOTHING_TO_ARCHIVE") return;
  assert.equal(result.skippedCurrentUser, true);
  assert.equal(state.statusChanges.length, 0);
  assert.equal(state.effects.length, 0);
});

test("уже архивированные исключаются из плана", async () => {
  const users = [
    makeUser("u1"),
    makeUser("u2", USER_STATUSES.ARCHIVED),
    makeUser("u3"),
  ];
  const { repository, state } = makeRepository(users);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: ["u1", "u2", "u3"],
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "ARCHIVED");
  if (result.status !== "ARCHIVED") return;
  assert.equal(result.archivedCount, 2);
  assert.deepEqual(state.statusChanges[0].userIds, ["u1", "u3"]);
});

test("свыше 100 архивированных: metadata.users подрезается до 100", async () => {
  const users = Array.from({ length: 150 }, (_, index) =>
    makeUser(`u${index + 1}`),
  );
  const { repository, state } = makeRepository(users);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: users.map((user) => user.id),
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "ARCHIVED");
  if (result.status !== "ARCHIVED") return;
  assert.equal(result.archivedCount, 150);
  const metadata = state.effects[0].audit?.metadata as { users: unknown[] };
  assert.equal(metadata.users.length, 100);
});

test("дубликаты в запросе схлопываются", async () => {
  const users = [makeUser("u1"), makeUser("u2")];
  const { repository } = makeRepository(users);
  const bulkArchive = createBulkArchiveUsers({ repository });

  const result = await bulkArchive({
    requestedUserIds: ["u1", "u1", "u2", "u2"],
    currentUserId: "admin-1",
    audit: AUDIT,
  });

  assert.equal(result.status, "ARCHIVED");
  if (result.status !== "ARCHIVED") return;
  assert.equal(result.requestedCount, 2);
  assert.equal(result.archivedCount, 2);
});
