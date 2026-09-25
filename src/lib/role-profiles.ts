import type { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import {
  PERMISSIONS,
  ROLES,
  ROLE_PERMISSIONS,
  STANDARD_ROLE_NAMES,
  type Permission,
  normalizePermissions,
} from "@/lib/roles";

function stringifyPermissions(permissions: Permission[]) {
  return JSON.stringify([...new Set(permissions)].sort());
}

const LEGACY_SYSTEM_ROLE_PERMISSION_MIGRATIONS: Array<{
  name: string;
  from: Permission[];
  to: Permission[];
}> = [
  {
    name: ROLES.ADMIN,
    from: Object.values(PERMISSIONS).filter(
      (permission) => permission !== PERMISSIONS.LEARNING_MATERIALS_PROGRESS
    ),
    to: ROLE_PERMISSIONS[ROLES.ADMIN] ?? [],
  },
  {
    name: STANDARD_ROLE_NAMES.SYSTEM_ADMIN,
    from: Object.values(PERMISSIONS).filter(
      (permission) => permission !== PERMISSIONS.LEARNING_MATERIALS_PROGRESS
    ),
    to: ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.SYSTEM_ADMIN] ?? [],
  },
  {
    name: STANDARD_ROLE_NAMES.STUDENT,
    from: [PERMISSIONS.COURSES_VIEW],
    to: [PERMISSIONS.COURSES_VIEW, PERMISSIONS.LEARNING_MATERIALS_PROGRESS],
  },
  {
    name: STANDARD_ROLE_NAMES.STUDENT,
    from: [PERMISSIONS.COURSES_VIEW, PERMISSIONS.LEARNING_KNOWLEDGE_CHECK],
    to: [
      PERMISSIONS.COURSES_VIEW,
      PERMISSIONS.LEARNING_MATERIALS_PROGRESS,
      PERMISSIONS.LEARNING_KNOWLEDGE_CHECK,
    ],
  },
];

function samePermissions(left: Permission[], right: Permission[]) {
  if (left.length !== right.length) return false;

  const leftSorted = [...left].sort();
  const rightSorted = [...right].sort();
  return leftSorted.every((value, index) => value === rightSorted[index]);
}

function migrateLegacySystemRolePermissions(name: string, permissions: Permission[]) {
  const normalized = normalizePermissions(permissions);
  const migration = LEGACY_SYSTEM_ROLE_PERMISSION_MIGRATIONS.find(
    (item) => item.name === name && samePermissions(normalized, item.from)
  );
  return migration ? normalizePermissions(migration.to) : normalized;
}

export function parsePermissionsJson(raw: string): Permission[] {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return normalizePermissions(parsed.map((value) => String(value)));
  } catch {
    return [];
  }
}

export const SYSTEM_ROLE_PROFILES: Array<{
  name: string;
  permissions: Permission[];
}> = [
  { name: ROLES.ADMIN, permissions: ROLE_PERMISSIONS[ROLES.ADMIN] ?? [] },
  {
    name: STANDARD_ROLE_NAMES.SYSTEM_ADMIN,
    permissions: ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.SYSTEM_ADMIN] ?? [],
  },
  {
    name: STANDARD_ROLE_NAMES.STUDENT,
    permissions: ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.STUDENT] ?? [],
  },
  {
    name: STANDARD_ROLE_NAMES.HR,
    permissions: ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.HR] ?? [],
  },
  {
    name: STANDARD_ROLE_NAMES.COURSE_AUTHOR,
    permissions: ROLE_PERMISSIONS[STANDARD_ROLE_NAMES.COURSE_AUTHOR] ?? [],
  },
];

export async function ensureSystemRoleProfiles() {
  const profiles = await Promise.all(
    SYSTEM_ROLE_PROFILES.map((role) =>
      prisma.roleProfile.upsert({
        where: { name: role.name },
        create: {
          name: role.name,
          isSystem: true,
          permissionsJson: stringifyPermissions(role.permissions),
        },
        update: {
          // Existing system roles keep admin-edited permissions.
          isSystem: true,
        },
      }),
    ),
  );

  await Promise.all(
    profiles.map(async (profile) => {
      const permissions = migrateLegacySystemRolePermissions(
        profile.name,
        parsePermissionsJson(profile.permissionsJson)
      );

      if (stringifyPermissions(permissions) === profile.permissionsJson) return;

      await prisma.roleProfile.update({
        where: { id: profile.id },
        data: { permissionsJson: stringifyPermissions(permissions) },
      });
    })
  );
}

export async function getPermissionsForRoleName(
  role: string | undefined | null,
): Promise<Permission[]> {
  if (!role) return [];
  const profile = await prisma.roleProfile.findUnique({
    where: { name: role },
    select: { permissionsJson: true },
  });
  if (profile) return migrateLegacySystemRolePermissions(role, parsePermissionsJson(profile.permissionsJson));
  return ROLE_PERMISSIONS[role] ?? [];
}

export async function getPermissionsForRoleNames(
  roleNames: string[] | undefined | null,
  client: Pick<Prisma.TransactionClient, "roleProfile"> = prisma,
): Promise<Permission[]> {
  if (!roleNames?.length) return [];

  const uniqueRoleNames = [...new Set(roleNames.filter(Boolean))];
  const profiles = await client.roleProfile.findMany({
    where: { name: { in: uniqueRoleNames } },
    select: { name: true, permissionsJson: true },
  });

  const byName = new Map(
    profiles.map((profile) => [
      profile.name,
      migrateLegacySystemRolePermissions(profile.name, parsePermissionsJson(profile.permissionsJson)),
    ]),
  );

  return normalizePermissions(
    uniqueRoleNames.flatMap(
      (roleName) => byName.get(roleName) ?? ROLE_PERMISSIONS[roleName] ?? [],
    ),
  );
}
