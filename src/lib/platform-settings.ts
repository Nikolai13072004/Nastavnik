import { randomInt } from "crypto";
import { connection } from "next/server";
import type { PlatformSettings as PrismaPlatformSettings } from "@prisma/client";
import prisma from "@/lib/prisma";
import {
  DEFAULT_COURSE_ASSIGNED_EMAIL_TEMPLATE,
  DEFAULT_PLATFORM_EMAIL_TEMPLATES,
  parsePlatformHtmlEmailTemplateJson,
  type PlatformHtmlEmailTemplate,
} from "@/lib/email/template-settings";
import {
  DEFAULT_PLATFORM_DATE_FORMAT,
  DEFAULT_PLATFORM_TIME_FORMAT,
  DEFAULT_PLATFORM_TIME_ZONE,
  isPlatformDateFormat,
  isPlatformTimeFormat,
  isSupportedPlatformTimeZone,
} from "@/lib/platform-localization";

export const DEFAULT_LOGO_URL: string | null = null;
export const DEFAULT_MAINTENANCE_MESSAGE =
  "Платформа временно недоступна из-за технических работ. Попробуйте зайти позже.";

export const DEFAULT_PLATFORM_SETTINGS = {
  id: "default",
  siteName: "Smart LMS",
  siteDescription: "Корпоративное обучение сотрудников",
  logoUrl: DEFAULT_LOGO_URL,
  faviconUrl: null,
  supportEmail: null,
  feedbackEnabled: true,
  feedbackModerationEnabled: false,
  timeZone: DEFAULT_PLATFORM_TIME_ZONE,
  dateFormat: DEFAULT_PLATFORM_DATE_FORMAT,
  timeFormat: DEFAULT_PLATFORM_TIME_FORMAT,
  maintenanceMode: false,
  maintenanceMessage: null,
  smtpSettingsSource: "ENV",
  smtpHost: null,
  smtpPort: 587,
  smtpEncryption: "TLS",
  smtpLogin: null,
  smtpPasswordSet: false,
  smtpFromEmail: null,
  smtpFromName: null,
  welcomeEmailTemplate: DEFAULT_PLATFORM_EMAIL_TEMPLATES.welcome,
  passwordResetEmailTemplate: DEFAULT_PLATFORM_EMAIL_TEMPLATES.passwordReset,
  certificateEmailTemplate: DEFAULT_PLATFORM_EMAIL_TEMPLATES.certificate,
  courseAssignedEmailTemplate: DEFAULT_COURSE_ASSIGNED_EMAIL_TEMPLATE,
  passwordMinLength: 8,
  passwordRequireNumber: false,
  passwordRequireUppercase: false,
  passwordRequireSpecialChar: false,
  sessionMaxAgeMinutes: 30 * 24 * 60,
  sessionIdleTimeoutMinutes: 0,
  maxFailedLoginAttempts: 5,
  loginLockoutMinutes: 15,
  loginEventRetentionDays: 90,
  auditLogRetentionDays: 180,
  adminTotpRequired: false,
  userActivationInviteTtlDays: 7,
  courseInviteTtlDays: 7,
  courseRemindersEnabled: true,
  courseReminderNotStartedEnabled: true,
  courseReminderExpiringEnabled: true,
  courseReminderExpiredEnabled: true,
  courseReminderQuizFailedEnabled: true,
  courseReminderExpiringDays: 3,
} as const;

export type PlatformSettingsState = {
  id: string;
  siteName: string;
  siteDescription: string;
  logoUrl: string | null;
  faviconUrl: string | null;
  supportEmail: string | null;
  feedbackEnabled: boolean;
  feedbackModerationEnabled: boolean;
  timeZone: string;
  dateFormat: string;
  timeFormat: string;
  maintenanceMode: boolean;
  maintenanceMessage: string | null;
  smtpSettingsSource: "ENV" | "PLATFORM";
  smtpHost: string | null;
  smtpPort: number | null;
  smtpEncryption: string;
  smtpLogin: string | null;
  /**
   * Только признак того, что пароль задан. Самого пароля здесь нет и быть не должно:
   * это состояние целиком уезжает в браузер через props клиентской формы настроек.
   * За значением — `getSmtpCredentials()` из `@/lib/platform-smtp-credentials`.
   */
  smtpPasswordSet: boolean;
  smtpFromEmail: string | null;
  smtpFromName: string | null;
  welcomeEmailTemplate: PlatformHtmlEmailTemplate;
  passwordResetEmailTemplate: PlatformHtmlEmailTemplate;
  certificateEmailTemplate: PlatformHtmlEmailTemplate;
  courseAssignedEmailTemplate: PlatformHtmlEmailTemplate;
  passwordMinLength: number;
  passwordRequireNumber: boolean;
  passwordRequireUppercase: boolean;
  passwordRequireSpecialChar: boolean;
  sessionMaxAgeMinutes: number;
  sessionIdleTimeoutMinutes: number;
  maxFailedLoginAttempts: number;
  loginLockoutMinutes: number;
  loginEventRetentionDays: number;
  auditLogRetentionDays: number;
  adminTotpRequired: boolean;
  userActivationInviteTtlDays: number;
  courseInviteTtlDays: number;
  courseRemindersEnabled: boolean;
  courseReminderNotStartedEnabled: boolean;
  courseReminderExpiringEnabled: boolean;
  courseReminderExpiredEnabled: boolean;
  courseReminderQuizFailedEnabled: boolean;
  courseReminderExpiringDays: number;
};

export type PlatformSecuritySettingsState = Pick<
  PlatformSettingsState,
  | "passwordMinLength"
  | "passwordRequireNumber"
  | "passwordRequireUppercase"
  | "passwordRequireSpecialChar"
  | "sessionMaxAgeMinutes"
  | "sessionIdleTimeoutMinutes"
  | "maxFailedLoginAttempts"
  | "loginLockoutMinutes"
  | "loginEventRetentionDays"
  | "auditLogRetentionDays"
  | "adminTotpRequired"
  | "userActivationInviteTtlDays"
  | "courseInviteTtlDays"
>;

function normalizeInteger(
  value: number | null | undefined,
  fallback: number,
  min: number,
  max: number
) {
  if (!Number.isInteger(value)) return fallback;
  return Math.min(Math.max(value ?? fallback, min), max);
}

export async function getPlatformSettings(): Promise<PlatformSettingsState> {
  const record = await prisma.platformSettings.findUnique({
    where: { id: DEFAULT_PLATFORM_SETTINGS.id },
  });

  return toPlatformSettingsState(record);
}

/**
 * Преобразует запись настроек в состояние для UI.
 *
 * Функция намеренно вынесена отдельно и чиста: результат уезжает в браузер
 * (страница настроек передаёт его в клиентскую форму, а Next сериализует props
 * целиком), поэтому наличие секретов в нём проверяется unit-тестом.
 */
export function toPlatformSettingsState(
  record: PrismaPlatformSettings | null
): PlatformSettingsState {
  return {
    id: DEFAULT_PLATFORM_SETTINGS.id,
    siteName: record?.siteName?.trim() || DEFAULT_PLATFORM_SETTINGS.siteName,
    siteDescription: record?.siteDescription?.trim() || DEFAULT_PLATFORM_SETTINGS.siteDescription,
    logoUrl: record?.logoUrl?.trim() || DEFAULT_PLATFORM_SETTINGS.logoUrl,
    faviconUrl: record?.faviconUrl?.trim() || DEFAULT_PLATFORM_SETTINGS.faviconUrl,
    supportEmail: record?.supportEmail?.trim() || DEFAULT_PLATFORM_SETTINGS.supportEmail,
    feedbackEnabled: record?.feedbackEnabled ?? DEFAULT_PLATFORM_SETTINGS.feedbackEnabled,
    feedbackModerationEnabled:
      record?.feedbackModerationEnabled ?? DEFAULT_PLATFORM_SETTINGS.feedbackModerationEnabled,
    timeZone:
      record?.timeZone?.trim() && isSupportedPlatformTimeZone(record.timeZone.trim())
        ? record.timeZone.trim()
        : DEFAULT_PLATFORM_SETTINGS.timeZone,
    dateFormat:
      record?.dateFormat?.trim() && isPlatformDateFormat(record.dateFormat.trim())
        ? record.dateFormat.trim()
        : DEFAULT_PLATFORM_SETTINGS.dateFormat,
    timeFormat:
      record?.timeFormat?.trim() && isPlatformTimeFormat(record.timeFormat.trim())
        ? record.timeFormat.trim()
        : DEFAULT_PLATFORM_SETTINGS.timeFormat,
    maintenanceMode: record?.maintenanceMode ?? DEFAULT_PLATFORM_SETTINGS.maintenanceMode,
    maintenanceMessage: record?.maintenanceMessage?.trim() || DEFAULT_PLATFORM_SETTINGS.maintenanceMessage,
    smtpSettingsSource: record?.smtpSettingsSource === "PLATFORM" ? "PLATFORM" : DEFAULT_PLATFORM_SETTINGS.smtpSettingsSource,
    smtpHost: record?.smtpHost?.trim() || DEFAULT_PLATFORM_SETTINGS.smtpHost,
    smtpPort: record?.smtpPort ?? DEFAULT_PLATFORM_SETTINGS.smtpPort,
    smtpEncryption: record?.smtpEncryption?.trim() || DEFAULT_PLATFORM_SETTINGS.smtpEncryption,
    smtpLogin: record?.smtpLogin?.trim() || DEFAULT_PLATFORM_SETTINGS.smtpLogin,
    smtpPasswordSet: Boolean(record?.smtpPassword?.trim()),
    smtpFromEmail: record?.smtpFromEmail?.trim() || DEFAULT_PLATFORM_SETTINGS.smtpFromEmail,
    smtpFromName: record?.smtpFromName?.trim() || DEFAULT_PLATFORM_SETTINGS.smtpFromName,
    welcomeEmailTemplate: parsePlatformHtmlEmailTemplateJson(
      record?.welcomeEmailTemplateJson,
      DEFAULT_PLATFORM_SETTINGS.welcomeEmailTemplate
    ),
    passwordResetEmailTemplate: parsePlatformHtmlEmailTemplateJson(
      record?.passwordResetEmailTemplateJson,
      DEFAULT_PLATFORM_SETTINGS.passwordResetEmailTemplate
    ),
    certificateEmailTemplate: parsePlatformHtmlEmailTemplateJson(
      record?.certificateEmailTemplateJson,
      DEFAULT_PLATFORM_SETTINGS.certificateEmailTemplate
    ),
    courseAssignedEmailTemplate: parsePlatformHtmlEmailTemplateJson(
      record?.courseAssignedEmailTemplateJson,
      DEFAULT_PLATFORM_SETTINGS.courseAssignedEmailTemplate
    ),
    passwordMinLength: normalizeInteger(record?.passwordMinLength, DEFAULT_PLATFORM_SETTINGS.passwordMinLength, 8, 16),
    passwordRequireNumber: record?.passwordRequireNumber ?? DEFAULT_PLATFORM_SETTINGS.passwordRequireNumber,
    passwordRequireUppercase:
      record?.passwordRequireUppercase ?? DEFAULT_PLATFORM_SETTINGS.passwordRequireUppercase,
    passwordRequireSpecialChar:
      record?.passwordRequireSpecialChar ?? DEFAULT_PLATFORM_SETTINGS.passwordRequireSpecialChar,
    sessionMaxAgeMinutes: normalizeInteger(
      record?.sessionMaxAgeMinutes,
      DEFAULT_PLATFORM_SETTINGS.sessionMaxAgeMinutes,
      15,
      43200
    ),
    sessionIdleTimeoutMinutes: normalizeInteger(
      record?.sessionIdleTimeoutMinutes,
      DEFAULT_PLATFORM_SETTINGS.sessionIdleTimeoutMinutes,
      0,
      1440
    ),
    maxFailedLoginAttempts: normalizeInteger(
      record?.maxFailedLoginAttempts,
      DEFAULT_PLATFORM_SETTINGS.maxFailedLoginAttempts,
      1,
      20
    ),
    loginLockoutMinutes: normalizeInteger(
      record?.loginLockoutMinutes,
      DEFAULT_PLATFORM_SETTINGS.loginLockoutMinutes,
      1,
      1440
    ),
    loginEventRetentionDays: normalizeInteger(
      record?.loginEventRetentionDays,
      DEFAULT_PLATFORM_SETTINGS.loginEventRetentionDays,
      90,
      3650
    ),
    auditLogRetentionDays: normalizeInteger(
      record?.auditLogRetentionDays,
      DEFAULT_PLATFORM_SETTINGS.auditLogRetentionDays,
      90,
      3650
    ),
    adminTotpRequired: record?.adminTotpRequired ?? DEFAULT_PLATFORM_SETTINGS.adminTotpRequired,
    userActivationInviteTtlDays: normalizeInteger(
      record?.userActivationInviteTtlDays,
      DEFAULT_PLATFORM_SETTINGS.userActivationInviteTtlDays,
      1,
      365
    ),
    courseInviteTtlDays: normalizeInteger(
      record?.courseInviteTtlDays,
      DEFAULT_PLATFORM_SETTINGS.courseInviteTtlDays,
      1,
      365
    ),
    courseRemindersEnabled: record?.courseRemindersEnabled ?? DEFAULT_PLATFORM_SETTINGS.courseRemindersEnabled,
    courseReminderNotStartedEnabled:
      record?.courseReminderNotStartedEnabled ?? DEFAULT_PLATFORM_SETTINGS.courseReminderNotStartedEnabled,
    courseReminderExpiringEnabled:
      record?.courseReminderExpiringEnabled ?? DEFAULT_PLATFORM_SETTINGS.courseReminderExpiringEnabled,
    courseReminderExpiredEnabled:
      record?.courseReminderExpiredEnabled ?? DEFAULT_PLATFORM_SETTINGS.courseReminderExpiredEnabled,
    courseReminderQuizFailedEnabled:
      record?.courseReminderQuizFailedEnabled ?? DEFAULT_PLATFORM_SETTINGS.courseReminderQuizFailedEnabled,
    courseReminderExpiringDays: normalizeInteger(
      record?.courseReminderExpiringDays,
      DEFAULT_PLATFORM_SETTINGS.courseReminderExpiringDays,
      1,
      60
    ),
  };
}

export async function getPlatformBranding() {
  // Branding comes from PostgreSQL and must not be queried during a Docker build.
  await connection();
  const settings = await getPlatformSettings();
  return {
    siteName: settings.siteName,
    siteDescription: settings.siteDescription,
    logoUrl: settings.logoUrl,
    faviconUrl: settings.faviconUrl,
    supportEmail: settings.supportEmail,
  };
}

export async function getPlatformSecuritySettings(): Promise<PlatformSecuritySettingsState> {
  const settings = await getPlatformSettings();
  return {
    passwordMinLength: settings.passwordMinLength,
    passwordRequireNumber: settings.passwordRequireNumber,
    passwordRequireUppercase: settings.passwordRequireUppercase,
    passwordRequireSpecialChar: settings.passwordRequireSpecialChar,
    sessionMaxAgeMinutes: settings.sessionMaxAgeMinutes,
    sessionIdleTimeoutMinutes: settings.sessionIdleTimeoutMinutes,
    maxFailedLoginAttempts: settings.maxFailedLoginAttempts,
    loginLockoutMinutes: settings.loginLockoutMinutes,
    loginEventRetentionDays: settings.loginEventRetentionDays,
    auditLogRetentionDays: settings.auditLogRetentionDays,
    adminTotpRequired: settings.adminTotpRequired,
    userActivationInviteTtlDays: settings.userActivationInviteTtlDays,
    courseInviteTtlDays: settings.courseInviteTtlDays,
  };
}

export async function getPlatformMaintenanceSettings() {
  const settings = await getPlatformSettings();
  return {
    maintenanceMode: settings.maintenanceMode,
    maintenanceMessage: settings.maintenanceMessage,
  };
}

export function resolveMaintenanceMessage(message?: string | null) {
  return message?.trim() || DEFAULT_MAINTENANCE_MESSAGE;
}

export function validatePasswordAgainstPolicy(
  password: string,
  settings: PlatformSecuritySettingsState
) {
  if (password.length < settings.passwordMinLength) {
    return `Пароль должен быть не короче ${settings.passwordMinLength} символов.`;
  }

  if (settings.passwordRequireNumber && !/\p{N}/u.test(password)) {
    return "Пароль должен содержать хотя бы одну цифру.";
  }

  if (settings.passwordRequireUppercase && !/\p{Lu}/u.test(password)) {
    return "Пароль должен содержать хотя бы одну заглавную букву.";
  }

  if (settings.passwordRequireSpecialChar && !/[^\p{L}\p{N}]/u.test(password)) {
    return "Пароль должен содержать хотя бы один спецсимвол.";
  }

  return null;
}

export function buildPasswordPolicyHint(settings: PlatformSecuritySettingsState) {
  const parts = [`Минимум ${settings.passwordMinLength} символов`];
  if (settings.passwordRequireNumber) parts.push("хотя бы одна цифра");
  if (settings.passwordRequireUppercase) parts.push("хотя бы одна заглавная буква");
  if (settings.passwordRequireSpecialChar) parts.push("хотя бы один спецсимвол");
  return `${parts.join(", ")}.`;
}

const LOWERCASE_PASSWORD_CHARS = "abcdefghijkmnopqrstuvwxyz";
const UPPERCASE_PASSWORD_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const DIGIT_PASSWORD_CHARS = "23456789";
const SPECIAL_PASSWORD_CHARS = "!@$%*_-";

function pickRandomChar(chars: string) {
  return chars[randomInt(chars.length)];
}

function shuffleChars(chars: string[]) {
  for (let index = chars.length - 1; index > 0; index -= 1) {
    const swapIndex = randomInt(index + 1);
    [chars[index], chars[swapIndex]] = [chars[swapIndex], chars[index]];
  }
}

export function generatePasswordForPolicy(settings: PlatformSecuritySettingsState) {
  const passwordChars = [pickRandomChar(LOWERCASE_PASSWORD_CHARS)];

  if (settings.passwordRequireUppercase) {
    passwordChars.push(pickRandomChar(UPPERCASE_PASSWORD_CHARS));
  }

  if (settings.passwordRequireNumber) {
    passwordChars.push(pickRandomChar(DIGIT_PASSWORD_CHARS));
  }

  if (settings.passwordRequireSpecialChar) {
    passwordChars.push(pickRandomChar(SPECIAL_PASSWORD_CHARS));
  }

  const allChars =
    LOWERCASE_PASSWORD_CHARS +
    UPPERCASE_PASSWORD_CHARS +
    DIGIT_PASSWORD_CHARS +
    SPECIAL_PASSWORD_CHARS;
  const targetLength = Math.max(settings.passwordMinLength, 12, passwordChars.length + 4);

  while (passwordChars.length < targetLength) {
    passwordChars.push(pickRandomChar(allChars));
  }

  shuffleChars(passwordChars);
  return passwordChars.join("");
}

export async function pruneExpiredLoginEvents(retentionDays: number) {
  const safeRetentionDays = Math.max(retentionDays, 1);
  const threshold = new Date(Date.now() - safeRetentionDays * 24 * 60 * 60 * 1000);

  await prisma.loginEvent.deleteMany({
    where: {
      createdAt: {
        lt: threshold,
      },
    },
  });
}
