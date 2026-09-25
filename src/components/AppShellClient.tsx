"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { signOut } from "next-auth/react";
import { savePreferredRolePreference } from "@/app/actions/user-preference-actions";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  PERMISSIONS,
  ROLES,
  ROLE_LABELS,
  ROLE_PERMISSIONS,
  hasPermission,
  hasRole,
  isPlatformAdminRole,
  isStaffRole,
} from "@/lib/roles";

type UserState = {
  id: string;
  name: string | null;
  email: string | null;
  avatarUrl: string | null;
  role: string | null;
  roles: string[];
  permissions: string[];
  preferredRole: string | null;
  twoFactorVerified: boolean;
  twoFactorSetupRequired: boolean;
} | null;

type NavItem = {
  href: string;
  label: string;
  icon: NavIconKind;
  requiredPermission?: (typeof PERMISSIONS)[keyof typeof PERMISSIONS];
  studentSection?: "courses" | "catalog";
  group?: NavGroupKind;
};

type NavIconKind =
  | "home"
  | "courses"
  | "catalog"
  | "history"
  | "users"
  | "reports"
  | "audit"
  | "api"
  | "storage"
  | "analytics"
  | "settings";

// Группы бокового меню (решение F каркаса): плоский список сворачивается в
// смысловые разделы. Порядок здесь задаёт порядок групп в сайдбаре; пустые
// группы (у роли нет их пунктов) при рендере пропускаются.
type NavGroupKind = "overview" | "learning" | "people" | "analytics" | "system";

const NAV_GROUPS: { key: NavGroupKind; label: string }[] = [
  { key: "overview", label: "Обзор" },
  { key: "learning", label: "Обучение" },
  { key: "people", label: "Люди и доступы" },
  { key: "analytics", label: "Аналитика" },
  { key: "system", label: "Система" },
];

type BrandingState = {
  siteName: string;
  siteDescription: string;
  logoUrl: string | null;
  faviconUrl: string | null;
  supportEmail: string | null;
};

type RoleChoice = {
  value: string;
  label: string;
};

const BASE_NAV_ITEMS: NavItem[] = [
  { href: "/courses", label: "Курсы", icon: "courses", group: "learning" },
  { href: "/certificates", label: "Мои сертификаты", icon: "history", group: "learning" },
  {
    href: "/admin/certificates",
    label: "Реестр сертификатов",
    icon: "reports",
    requiredPermission: PERMISSIONS.CERTIFICATES_MANAGE,
    group: "learning",
  },
  {
    href: "/admin/users-groups",
    label: "Пользователи",
    icon: "users",
    requiredPermission: PERMISSIONS.USERS_VIEW,
    group: "people",
  },
  // Решение C каркаса: сущности, раньше спрятанные за под-табами
  // AdminUsersSubtabs, выведены в видимое меню. Отдельные маршруты (чистая
  // подсветка активного пункта). «Группы» остаются внутри «Пользователей»
  // (делят маршрут /admin/users-groups, свои внутренние табы).
  {
    href: "/admin/roles",
    label: "Роли",
    icon: "users",
    requiredPermission: PERMISSIONS.USERS_EDIT_ACCESS_LEVEL,
    group: "people",
  },
  {
    href: "/admin/departments",
    label: "Подразделения",
    icon: "users",
    requiredPermission: PERMISSIONS.DEPARTMENTS_VIEW,
    group: "people",
  },
  {
    href: "/admin/organizations",
    label: "Организации",
    icon: "users",
    requiredPermission: PERMISSIONS.ORGANIZATIONS_VIEW,
    group: "people",
  },
  {
    href: "/admin/reports",
    label: "Отчеты",
    icon: "reports",
    requiredPermission: PERMISSIONS.REPORTS_VIEW,
    group: "analytics",
  },
  {
    href: "/admin/attention",
    label: "Внимание",
    icon: "reports",
    requiredPermission: PERMISSIONS.REPORTS_VIEW,
    group: "analytics",
  },
  {
    href: "/analytics",
    label: "Аналитика",
    icon: "analytics",
    requiredPermission: PERMISSIONS.REPORTS_VIEW,
    group: "analytics",
  },
];

const STUDENT_NAV_ITEMS: NavItem[] = [
  {
    href: "/courses?tab=assigned",
    label: "Мои курсы",
    icon: "courses",
    studentSection: "courses",
  },
  {
    href: "/courses?tab=catalog",
    label: "Каталог",
    icon: "catalog",
    studentSection: "catalog",
  },
  {
    href: "/certificates",
    label: "Мои сертификаты",
    icon: "history",
  },
];

function isActive(pathname: string, href: string) {
  const hrefPathname = href.split("?", 1)[0];
  if (href === "/") return pathname === "/";
  return pathname === hrefPathname || pathname.startsWith(`${hrefPathname}/`);
}

function initials(input: string) {
  const parts = input.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "U";
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

export function AppShellClient({
  children,
  branding,
  user,
}: {
  children: React.ReactNode;
  branding: BrandingState;
  user: UserState;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();

  const isAuthPage =
    pathname === "/login" ||
    pathname === "/forgot-password" ||
    pathname.startsWith("/reset-password/") ||
    pathname.startsWith("/activate/");
  const isInvitePage = pathname.startsWith("/invite/");
  const isTwoFactorSetupPage = pathname === "/auth/2fa/setup";
  const role = user?.role ?? null;
  const roles = user?.roles;
  const effectiveRoles = useMemo(
    () => (roles?.length ? roles : role ? [role] : []),
    [role, roles],
  );
  const roleChoices = useMemo(
    () =>
      Array.from(new Set(effectiveRoles)).map((value) => ({
        value,
        label: roleDisplayName(value),
      })),
    [effectiveRoles],
  );
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const preferredRole = user?.preferredRole ?? null;
  const displayName = user?.name ?? user?.email ?? "Пользователь";
  const avatarUrl = user?.avatarUrl ?? null;
  const currentTab = searchParams.get("tab");
  const currentSource = searchParams.get("from");
  const selectedRoleIsValid = Boolean(
    selectedRole && roleChoices.some((choice) => choice.value === selectedRole),
  );
  const preferredRoleIsValid = Boolean(
    preferredRole && roleChoices.some((choice) => choice.value === preferredRole),
  );
  const inferredRole = inferRoleFromLocation(roleChoices, pathname, currentTab, currentSource);
  const fallbackRole = roleChoices.find((choice) => choice.value !== ROLES.STUDENT)?.value ?? roleChoices[0]?.value ?? null;
  const activeRole = selectedRoleIsValid ? selectedRole : inferredRole ?? (preferredRoleIsValid ? preferredRole : fallbackRole);
  const activeRoleChoice = activeRole ? roleChoices.find((choice) => choice.value === activeRole) : null;
  const activeRoles = activeRole ? [activeRole] : effectiveRoles;
  const activeRolePermissions =
    activeRole && ROLE_PERMISSIONS[activeRole] ? undefined : user?.permissions ?? [];
  const hasStudentRole = hasRole(effectiveRoles, ROLES.STUDENT);
  const activeHasStudentRole = hasRole(activeRoles, ROLES.STUDENT);
  const isStudentPortal =
    activeHasStudentRole && !isStaffRole(activeRoles);
  const canOpenAdminHome =
    !isStudentPortal && isPlatformAdminRole(activeRoles);
  const canOpenHrHome =
    !isStudentPortal &&
    !canOpenAdminHome &&
    hasPermission(
      activeRoles,
      PERMISSIONS.REPORTS_VIEW,
      activeRolePermissions,
    );
  const homeHref = isStudentPortal
    ? "/courses?tab=assigned"
    : canOpenAdminHome || canOpenHrHome
      ? "/"
      : "/courses";
  const shellContainerClass = isStudentPortal ? "max-w-6xl" : "max-w-[1440px]";

  const handleRoleChange = async (nextRole: string) => {
    setSelectedRole(nextRole);
    try {
      await savePreferredRolePreference(nextRole);
    } catch (error) {
      console.error("Failed to save preferred role", error);
      setSelectedRole(preferredRoleIsValid ? preferredRole : null);
      return;
    }
    router.push(roleHomeHref(nextRole, user?.permissions ?? []));
    router.refresh();
  };

  const handleSignOut = async () => {
    await signOut({ redirect: false });
    window.location.href = "/login";
  };

  const navItems = (() => {
    if (isStudentPortal) return STUDENT_NAV_ITEMS;

    const items: NavItem[] = [...BASE_NAV_ITEMS];
    if (canOpenAdminHome) {
      items.splice(1, 0, {
        href: "/history",
        label: "История",
        icon: "history",
        group: "analytics",
      });
      items.splice(4, 0, {
        href: "/admin/audit-log",
        label: "Аудит",
        icon: "audit",
        group: "system",
      });
      items.splice(5, 0, {
        href: "/admin/api",
        label: "API",
        icon: "api",
        group: "system",
      });
      items.splice(6, 0, {
        href: "/admin/storage",
        label: "Хранилище",
        icon: "storage",
        group: "system",
      });
      items.push({
        href: "/admin/settings",
        label: "Настройки",
        icon: "settings",
        group: "system",
      });
      items.unshift({ href: "/", label: "Главная", icon: "home", group: "overview" });
    } else if (canOpenHrHome) {
      items.unshift({ href: "/", label: "Главная", icon: "home", group: "overview" });
    }

    if (activeHasStudentRole) {
      const insertIndex =
        items.findIndex((item) => item.href === "/") >= 0 ? 1 : 0;
      items.splice(insertIndex, 0, {
        href: "/courses?tab=assigned",
        label: "Мои курсы",
        icon: "courses",
        studentSection: "courses",
        group: "learning",
      });
    }

    return items.filter(
      (item) =>
        !item.requiredPermission ||
        hasPermission(
          activeRoles,
          item.requiredPermission,
          activeRolePermissions,
        ),
    );
  })();
  const showTopNav = isStudentPortal
    ? navItems.length > 0
    : navItems.length > 1;

  if (!user || isAuthPage || isInvitePage || pathname === "/max") {
    return <>{children}</>;
  }

  if (isTwoFactorSetupPage || user.twoFactorSetupRequired) {
    return (
      <div className="min-h-screen bg-[var(--surface)]">
        <header className="border-b border-[var(--line)] bg-[var(--surface-raised)]">
          <div className="mx-auto flex h-16 max-w-5xl items-center justify-between px-4">
            <Link href="/" className="text-sm font-semibold text-[var(--ink)]">
              {branding.siteName}
            </Link>
            <button
              type="button"
              onClick={handleSignOut}
              className="rounded-md border border-[var(--line)] px-3 py-1.5 text-sm hover:bg-[var(--accent-soft)]"
            >
              Выйти
            </button>
          </div>
        </header>
        {children}
      </div>
    );
  }

  return (
    <div className="app-shell min-h-screen bg-[var(--canvas)] text-[var(--ink)]">
      <header className="app-header sticky top-0 z-40 border-b border-[var(--line)] bg-[var(--surface)] backdrop-blur">
        <div
          className={`relative mx-auto flex h-16 ${shellContainerClass} items-center gap-4 px-4 lg:px-6`}
        >
          <Link
            href={homeHref}
            aria-label={`Перейти на главную страницу ${branding.siteName}`}
            title={`Перейти на главную страницу ${branding.siteName}`}
            className="flex items-center gap-2"
          >
            {branding.logoUrl ? (
              <Image
                src={branding.logoUrl}
                alt={branding.siteName}
                width={120}
                height={32}
                className="h-8 w-auto shrink-0 object-contain"
                priority
                referrerPolicy="no-referrer"
              />
            ) : (
              <span className="text-lg font-semibold text-[var(--ink)]">
                {branding.siteName}
              </span>
            )}
            {branding.logoUrl && (
              <span className="hidden text-sm font-medium text-[var(--ink-muted)] xl:inline">
                {branding.siteName}
              </span>
            )}
          </Link>

          <div className="ml-auto hidden items-center justify-end gap-3 md:flex">
            <ThemeToggle />
            <div className="flex flex-col items-end gap-1 text-right">
              <div className="text-sm font-medium">{displayName}</div>
              {roleChoices.length > 1 && activeRoleChoice ? (
                <RoleMenu
                  roles={roleChoices}
                  activeRole={activeRoleChoice.value}
                  activeLabel={activeRoleChoice.label}
                  onSelect={handleRoleChange}
                />
              ) : activeRoleChoice ? (
                <div className="text-xs text-[var(--ink-muted)]">{activeRoleChoice.label}</div>
              ) : null}
            </div>
            <div className="inline-flex h-9 w-9 items-center justify-center overflow-hidden rounded-full bg-[var(--accent-strong)] text-xs font-semibold text-white">
              {avatarUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={avatarUrl}
                  alt={displayName}
                  className="h-full w-full object-cover"
                />
              ) : (
                initials(displayName)
              )}
            </div>
            <button
              type="button"
              onClick={handleSignOut}
              className="rounded-md border border-[var(--line)] px-3 py-1.5 text-sm hover:bg-[var(--accent-soft)]"
            >
              Выйти
            </button>
          </div>
        </div>
      </header>

      {isStudentPortal && showTopNav && (
        <div className="sticky top-16 z-30 border-b border-[var(--line)] bg-[var(--surface-raised)] backdrop-blur">
          <nav
            className={`mx-auto flex w-full ${shellContainerClass} gap-2 overflow-x-auto px-4 py-3 lg:px-6`}
          >
            {navItems.map((item) => {
              const active = isNavItemActive(
                pathname,
                currentTab,
                currentSource,
                item,
                hasStudentRole,
              );
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`inline-flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition ${
                    active
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--ink)] hover:bg-[var(--accent-soft)]"
                  }`}
                >
                  <NavItemLabel item={item} />
                </Link>
              );
            })}
          </nav>
        </div>
      )}

      {!isStudentPortal && navItems.length > 0 && (
        <div className="border-b border-[var(--line)] bg-[var(--surface-raised)] lg:hidden">
          <nav className="mx-auto flex w-full max-w-[1440px] gap-2 overflow-x-auto px-4 py-3">
            {navItems.map((item) => {
              const active = isNavItemActive(
                pathname,
                currentTab,
                currentSource,
                item,
                hasStudentRole,
              );
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`inline-flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition ${
                    active
                      ? "bg-[var(--accent)] text-white"
                      : "text-[var(--ink)] hover:bg-[var(--accent-soft)]"
                  }`}
                >
                  <NavItemLabel item={item} />
                </Link>
              );
            })}
          </nav>
        </div>
      )}

      {isStudentPortal ? (
        <main
          className={`app-main mx-auto min-h-[calc(100vh-64px)] w-full ${shellContainerClass} p-4 lg:p-6`}
        >
          {children}
        </main>
      ) : (
        <div className="mx-auto flex w-full max-w-[1440px]">
          <aside className="app-sidebar hidden min-h-[calc(100vh-64px)] w-72 flex-col border-r border-[var(--line)] bg-[var(--aurora-sidebar)] text-white/90 lg:flex">
            <nav className="space-y-5 p-4">
              {NAV_GROUPS.map((grp) => {
                const groupItems = navItems.filter(
                  (item) => (item.group ?? "learning") === grp.key,
                );
                if (groupItems.length === 0) return null;
                return (
                  <div key={grp.key}>
                    <div className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-white/50">
                      {grp.label}
                    </div>
                    <ul className="space-y-1">
                      {groupItems.map((item) => {
                        const active = isNavItemActive(
                          pathname,
                          currentTab,
                          currentSource,
                          item,
                          hasStudentRole,
                        );
                        return (
                          <li key={item.href}>
                            <Link
                              href={item.href}
                              className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm transition ${
                                active
                                  ? "bg-[var(--aurora-sidebar-active)] text-white"
                                  : "text-white/80 hover:bg-[var(--aurora-sidebar-hover)] hover:text-white"
                              }`}
                            >
                              <NavItemLabel item={item} />
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              })}
            </nav>
          </aside>
          <main className="app-main min-h-[calc(100vh-64px)] min-w-0 flex-1 p-4 lg:p-6">
            {children}
          </main>
        </div>
      )}
    </div>
  );
}

function NavItemLabel({ item }: { item: NavItem }) {
  return (
    <>
      <NavIcon kind={item.icon} className="h-4 w-4 shrink-0" />
      <span>{item.label}</span>
    </>
  );
}

function RoleMenu({
  roles,
  activeRole,
  activeLabel,
  onSelect,
}: {
  roles: RoleChoice[];
  activeRole: string;
  activeLabel: string;
  onSelect: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div ref={menuRef} className="relative text-xs text-[var(--ink-muted)]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex rounded px-1 py-0.5 font-medium text-[var(--info)] underline-offset-2 hover:bg-[var(--info-soft)] hover:underline"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {activeLabel}
      </button>
      {open ? (
        <div className="absolute right-0 top-7 z-50 grid min-w-52 gap-1 rounded-lg border border-[var(--line)] bg-[var(--surface-raised)] p-1.5 text-left shadow-lg" role="menu">
          {roles.map((role) => {
            const active = role.value === activeRole;
            return (
              <button
                key={role.value}
                type="button"
                onClick={() => {
                  setOpen(false);
                  onSelect(role.value);
                }}
                className={`rounded-md px-3 py-2 text-left text-xs font-medium ${
                  active ? "bg-[var(--info-soft)] text-[var(--info)]" : "text-[var(--ink)] hover:bg-[var(--accent-soft)]"
                }`}
                aria-current={active ? "true" : undefined}
                role="menuitem"
              >
                {role.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function roleDisplayName(role: string) {
  return ROLE_LABELS[role] ?? role;
}

function roleHomeHref(role: string, explicitPermissions: string[]) {
  if (role === ROLES.STUDENT) return "/courses?tab=assigned";
  const permissions = ROLE_PERMISSIONS[role] ? undefined : explicitPermissions;
  if (isPlatformAdminRole([role])) return "/";
  if (hasPermission([role], PERMISSIONS.REPORTS_VIEW, permissions)) return "/";
  return "/courses";
}

function inferRoleFromLocation(
  roles: RoleChoice[],
  pathname: string,
  tab: string | null,
  source: string | null,
) {
  const hasStudentChoice = roles.some((role) => role.value === ROLES.STUDENT);
  const isLearningLocation =
    (pathname === "/courses" && isLearnerCoursesTab(tab)) ||
    (isStudentCoursePath(pathname) && (source === "assigned" || source === "catalog"));

  if (hasStudentChoice && isLearningLocation) return ROLES.STUDENT;
  return null;
}

function isStudentCoursePath(pathname: string) {
  return /^\/courses\/[^/]+(\/(about|feedback|survey|quiz)(\/|$))?$/.test(pathname);
}

function isLearnerCoursesTab(tab: string | null) {
  return tab === "assigned" || tab === "completed" || tab === "catalog";
}

function isNavItemActive(
  pathname: string,
  tab: string | null,
  source: string | null,
  item: NavItem,
  hasStudentNavItem: boolean,
) {
  if (item.studentSection) {
    return isStudentNavItemActive(pathname, tab, source, item);
  }

  if (
    item.href === "/courses" &&
    ((pathname === "/courses" && isLearnerCoursesTab(tab)) ||
      (hasStudentNavItem && isStudentCoursePath(pathname)))
  ) {
    return false;
  }

  return isActive(pathname, item.href);
}

function isStudentNavItemActive(
  pathname: string,
  tab: string | null,
  source: string | null,
  item: NavItem,
) {
  const isCourseOpenedFromCatalog =
    source === "catalog" && isStudentCoursePath(pathname);
  const itemTab = new URLSearchParams(item.href.split("?", 2)[1] ?? "").get("tab");
  const requiresLearnerTab = item.studentSection === "courses" && itemTab === "assigned";

  if (item.studentSection === "catalog") {
    return (
      (pathname === "/courses" && tab === "catalog") ||
      isCourseOpenedFromCatalog
    );
  }

  if (item.studentSection === "courses") {
    return (
      (pathname === "/courses" &&
        (tab === "assigned" || tab === "completed" || (!requiresLearnerTab && tab !== "catalog"))) ||
      (!isCourseOpenedFromCatalog &&
        isStudentCoursePath(pathname) &&
        (!requiresLearnerTab || source === "assigned"))
    );
  }

  return isActive(pathname, item.href);
}

function NavIcon({
  kind,
  className,
}: {
  kind: NavIconKind;
  className?: string;
}) {
  switch (kind) {
    case "home":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M3.75 10.5 12 4l8.25 6.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M6.75 9.75V20h10.5V9.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "courses":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M5 6.25A2.25 2.25 0 0 1 7.25 4h10A1.75 1.75 0 0 1 19 5.75v12.5A1.75 1.75 0 0 0 17.25 20h-10A2.25 2.25 0 0 1 5 17.75Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path d="M8 8.25h7.5M8 11.75h7.5" strokeLinecap="round" />
        </svg>
      );
    case "catalog":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M4.75 6.75A2 2 0 0 1 6.75 4.75h10.5a2 2 0 0 1 2 2v10.5a2 2 0 0 1-2 2H6.75a2 2 0 0 1-2-2Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M8 8h3.25v3.25H8ZM12.75 8H16v3.25h-3.25ZM8 12.75h3.25V16H8ZM12.75 12.75H16V16h-3.25Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "history":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M4.75 12a7.25 7.25 0 1 0 2.12-5.13"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M4.75 6.75v3.75H8.5M12 8.5V12l2.75 1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "users":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM16.75 10a2.25 2.25 0 1 0 0-4.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M4.5 18.5a4.5 4.5 0 0 1 9 0M14.5 18.5a3.75 3.75 0 0 1 5-3.54"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "reports":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path d="M5 19.25h14" strokeLinecap="round" />
          <path
            d="M7.5 16.75v-5.5M12 16.75V8.5M16.5 16.75v-8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "audit":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M12 4.25 18.25 6.5v5.75c0 3.38-2.52 6.42-6.25 7.5-3.73-1.08-6.25-4.12-6.25-7.5V6.5Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="m9.5 12 1.75 1.75L14.75 10"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "api":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="m9.25 7.25-4 4.75 4 4.75M14.75 7.25l4 4.75-4 4.75M12.5 5.5l-1 13"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "storage":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M4.75 7.5h14.5v9a2 2 0 0 1-2 2H6.75a2 2 0 0 1-2-2Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M7.5 7.5V6a1.25 1.25 0 0 1 1.25-1.25h6.5A1.25 1.25 0 0 1 16.5 6v1.5M8.5 11.25h7"
            strokeLinecap="round"
          />
        </svg>
      );
    case "analytics":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path d="M5 18.5h14" strokeLinecap="round" />
          <path
            d="m6.5 15.5 3.25-3.25 3 2.25 4.75-6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M16.5 8.5h2v2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "settings":
      return (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className={className}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
        >
          <path
            d="M12 8.75a3.25 3.25 0 1 0 0 6.5 3.25 3.25 0 0 0 0-6.5Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="m19 12-.92.53a1 1 0 0 0-.48.87v1.05a1 1 0 0 1-.74.96l-1.06.27a1 1 0 0 0-.7.67l-.36 1.03a1 1 0 0 1-.91.65h-1.16a1 1 0 0 0-.8.4l-.66.88a1 1 0 0 1-1.08.35l-1.03-.36a1 1 0 0 0-.94.15l-.94.61a1 1 0 0 1-1.12-.06l-.89-.67a1 1 0 0 1-.37-1.07l.24-1.03a1 1 0 0 0-.18-.85l-.68-.95a1 1 0 0 1-.04-1.11l.54-.92a1 1 0 0 0 .1-.87l-.29-1.06a1 1 0 0 1 .33-1.03l.86-.72a1 1 0 0 1 1.1-.1l.97.49a1 1 0 0 0 .95.03l1.01-.44a1 1 0 0 1 1.06.24l.79.75a1 1 0 0 0 .82.26l1.09-.15a1 1 0 0 1 .98.49l.55.89a1 1 0 0 0 .74.46l1.08.17a1 1 0 0 1 .81.78l.25 1.05a1 1 0 0 0 .56.69L19 12Z"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
  }
}
