import { getSmtpDeliveryConfig } from "@/lib/platform-smtp-credentials";
import { resolveSmtpAuth } from "@/lib/email/smtp-auth-resolution";
import {
  createSmtpTransport,
  getSmtpAuthType,
  hasSmtpOAuth2Credentials,
  readSmtpOAuth2CredentialsFromEnv,
} from "@/lib/email/smtp-transport";

type SendEmailInput = {
  toEmail: string;
  toName?: string | null;
  subject: string;
  htmlBody: string;
  textBody: string;
};

function formatTo(input: SendEmailInput) {
  return input.toName ? "\"" + input.toName + "\" <" + input.toEmail + ">" : input.toEmail;
}

function readEnv(key: string) {
  const value = process.env[key]?.trim();
  return value || null;
}

function resolveEnvSmtpEncryption(portRaw: string | null | undefined) {
  const explicitEncryption = readEnv("SMTP_ENCRYPTION");
  if (explicitEncryption) return explicitEncryption.toUpperCase();

  const secure = readEnv("SMTP_SECURE")?.toLowerCase();
  if (secure === "true") return "SSL";
  if (secure === "false") return "TLS";
  if (portRaw === "465") return "SSL";

  return "TLS";
}

function hasFormattedAddress(value: string) {
  return /^.+<[^>]+>$/.test(value.trim());
}

function escapeAddressName(value: string) {
  return value.replaceAll(String.fromCharCode(34), String.fromCharCode(92, 34));
}

function formatFrom(emailOrHeader: string | null, name: string | null) {
  if (!emailOrHeader) return null;
  if (!name || hasFormattedAddress(emailOrHeader)) return emailOrHeader;
  return "\"" + escapeAddressName(name) + "\" <" + emailOrHeader + ">";
}

export async function sendEmail(input: SendEmailInput) {
  // The isolated MAX pilot has no email workflow. Keep delivery disabled even
  // if SMTP settings are later populated in its database or environment.
  if (process.env.MAX_DISABLE_EMAIL === "true") return;

  const provider = (process.env.EMAIL_PROVIDER ?? "stub").toLowerCase();
  // Вся конфигурация доставки читается одним запросом: хост, порт, источник и
  // учётные данные должны происходить из одного снимка настроек. Сборка их из
  // разных чтений базы даёт при ротации смесь вроде «старый хост + новый
  // пароль». Секретов нет в PlatformSettingsState намеренно — то состояние
  // уезжает в браузер вместе с props клиентской формы настроек.
  const smtp = await getSmtpDeliveryConfig();
  const usePlatformSettings = smtp.source === "PLATFORM";
  const envHost = readEnv("SMTP_HOST");
  const envPortRaw = readEnv("SMTP_PORT");
  const envUser = readEnv("SMTP_USER");
  const envPass = readEnv("SMTP_PASS");
  const envFromName = readEnv("SMTP_FROM_NAME");
  const envFrom = formatFrom(readEnv("SMTP_FROM"), envFromName || smtp.fromName);
  const settingsPortRaw = smtp.port ? String(smtp.port) : null;
  const settingsFrom = formatFrom(smtp.fromEmail, smtp.fromName || envFromName);

  const host = usePlatformSettings ? smtp.host || envHost : envHost || smtp.host;
  const portRaw = usePlatformSettings ? settingsPortRaw || envPortRaw : envPortRaw || settingsPortRaw;
  const { user, pass } = resolveSmtpAuth({
    usePlatformSettings,
    platform: { login: smtp.login, password: smtp.password },
    env: { login: envUser, password: envPass },
  });
  const from = usePlatformSettings ? settingsFrom || envFrom : envFrom || settingsFrom;
  const encryption = usePlatformSettings ? smtp.encryption : resolveEnvSmtpEncryption(envPortRaw || portRaw);
  const authType = getSmtpAuthType();
  const oauth2 = readSmtpOAuth2CredentialsFromEnv();
  const hasAuth = authType === "oauth2" ? hasSmtpOAuth2Credentials(oauth2) : Boolean(pass);
  const shouldUseStub = provider === "stub" && (!host || !portRaw || !user || !from || !hasAuth);

  if (shouldUseStub) {
    console.info("[email:stub]", {
      to: formatTo(input),
      subject: input.subject,
    });
    return;
  }

  if (provider === "stub") {
    console.info("[email:provider] SMTP settings loaded from platform settings.");
  }

  if (!host || !portRaw || !user || !from) {
    throw new Error("SMTP config is incomplete. Set SMTP_HOST, SMTP_PORT, SMTP_USER and SMTP_FROM.");
  }

  const port = Number(portRaw);
  if (!Number.isFinite(port) || port <= 0) {
    throw new Error("SMTP_PORT is invalid.");
  }

  const transporter =
    authType === "oauth2"
      ? createSmtpTransport({
          host,
          port,
          encryption,
          user,
          authType,
          clientId: requireOAuth2Value(oauth2.clientId, "SMTP_OAUTH_CLIENT_ID"),
          clientSecret: requireOAuth2Value(oauth2.clientSecret, "SMTP_OAUTH_CLIENT_SECRET"),
          refreshToken: requireOAuth2Value(oauth2.refreshToken, "SMTP_OAUTH_REFRESH_TOKEN"),
          accessToken: oauth2.accessToken ?? undefined,
          accessUrl: oauth2.accessUrl ?? undefined,
        })
      : createSmtpTransport({
          host,
          port,
          encryption,
          user,
          pass: requirePassword(pass),
        });

  await transporter.sendMail({
    from,
    to: formatTo(input),
    subject: input.subject,
    text: input.textBody,
    html: input.htmlBody,
  });
}

function requireOAuth2Value(value: string | null, envName: string) {
  if (!value) {
    throw new Error(`SMTP OAuth2 config is incomplete. Set ${envName}.`);
  }
  return value;
}

function requirePassword(value: string | null | undefined) {
  if (!value) {
    throw new Error("SMTP password config is incomplete. Set SMTP_PASS or save SMTP password in platform settings.");
  }
  return value;
}
