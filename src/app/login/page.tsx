import { redirect } from "next/navigation";
import Image from "next/image";
import { auth } from "@/auth";
import { getPlatformSettings, resolveMaintenanceMessage } from "@/lib/platform-settings";
import { LoginClientSection } from "./LoginClientSection";

type Props = { searchParams: Promise<{ callbackUrl?: string; notice?: string }> };

function safeCallbackUrl(raw?: string) {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export default async function LoginPage({ searchParams }: Props) {
  const [session, settings] = await Promise.all([auth(), getPlatformSettings()]);
  const sp = await searchParams;
  const callbackUrl = safeCallbackUrl(sp.callbackUrl);
  const maintenanceMessage = settings.maintenanceMode ? resolveMaintenanceMessage(settings.maintenanceMessage) : null;

  if (session?.user) {
    if (session.user.twoFactorSetupRequired) {
      redirect(`/auth/2fa/setup?callbackUrl=${encodeURIComponent(callbackUrl)}`);
    }

    redirect(callbackUrl);
  }

  return (
    <main className="mx-auto flex min-h-[calc(100vh-3.5rem)] max-w-md flex-col justify-center px-4 py-12">
      <div className="mb-6 flex justify-center">
        {settings.logoUrl ? (
          <Image
            src={settings.logoUrl}
            alt={settings.siteName}
            width={180}
            height={58}
            className="h-[3.6rem] w-auto object-contain"
            priority
            referrerPolicy="no-referrer"
          />
        ) : (
          <div className="text-center text-3xl font-semibold text-[var(--ink)]">{settings.siteName}</div>
        )}
      </div>
      <p className="text-center text-sm font-medium uppercase tracking-[0.2em] text-[var(--ink-muted)]">{settings.siteName}</p>
      <h1 className="mt-2 text-center text-2xl font-semibold">Вход в систему</h1>
      <p className="mt-3 text-center text-sm text-[var(--ink-muted)]">{settings.siteDescription}</p>

      {callbackUrl === "/connect-max" && (
        <p className="mt-4 text-center text-sm text-[var(--ink-muted)]">
          Войдите в свою учётную запись LMS, чтобы получить код привязки MAX.
          После входа откроется страница «Подключить свой MAX».
          Если у вас нет логина и пароля, обратитесь к HR.
        </p>
      )}

      {settings.maintenanceMode ? (
        <div className="mt-6 rounded-2xl border border-[var(--warning)] bg-[var(--warning-soft)] px-4 py-3 text-sm text-[var(--warning)]">
          <p className="font-medium">Режим обслуживания включен. Вход доступен только администраторам.</p>
          <p className="mt-1">{maintenanceMessage}</p>
        </div>
      ) : null}

      {sp.notice ? (
        <p className="mt-6 rounded-2xl border border-[var(--success)] bg-[var(--success-soft)] px-4 py-3 text-sm text-[var(--success)]">
          {sp.notice}
        </p>
      ) : null}

      <LoginClientSection callbackUrl={callbackUrl} />

      {settings.supportEmail ? (
        <p className="mt-6 text-center text-sm text-[var(--ink-muted)]">
          Поддержка:{" "}
          <a className="text-[var(--ink)] underline-offset-2 hover:underline" href={`mailto:${settings.supportEmail}`}>
            {settings.supportEmail}
          </a>
        </p>
      ) : null}
    </main>
  );
}
