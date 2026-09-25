import Image from "next/image";
import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";
import { getPlatformSettings, resolveMaintenanceMessage } from "@/lib/platform-settings";
import { buttonStyles } from "@/components/ui";

export default async function MaintenancePage() {
  await connection();
  const settings = await getPlatformSettings();

  if (!settings.maintenanceMode) {
    redirect("/login");
  }

  const message = resolveMaintenanceMessage(settings.maintenanceMessage);

  return (
    <main className="mx-auto flex min-h-[calc(100vh-3.5rem)] max-w-3xl flex-col justify-center px-4 py-12">
      <div className="rounded-[2rem] border border-[var(--line)] bg-[var(--surface-raised)] p-8 shadow-sm sm:p-10">
        <div className="flex flex-col items-center text-center">
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

          <p className="mt-6 text-sm font-medium uppercase tracking-[0.2em] text-[var(--ink-muted)]">Технические работы</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-[var(--ink)]">Платформа временно недоступна</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-[var(--ink-muted)]">{message}</p>
        </div>

        <div className="mt-8 grid gap-4 rounded-3xl border border-[var(--line)] bg-[var(--surface)] p-5 text-sm text-[var(--ink-muted)] sm:grid-cols-2">
          <div>
            <p className="font-medium text-[var(--ink)]">Что происходит</p>
            <p className="mt-2">
              Доступ к платформе временно ограничен на время обновления или сервисных работ. Во время режима
              обслуживания продолжить работу могут только администраторы.
            </p>
          </div>
          <div>
            <p className="font-medium text-[var(--ink)]">Нужен срочный доступ?</p>
            <p className="mt-2">Если вы администратор платформы, можно перейти на страницу входа и авторизоваться.</p>
            {settings.supportEmail ? (
              <p className="mt-2">
                Поддержка:{" "}
                <a className="text-[var(--ink)] underline decoration-[var(--line)] underline-offset-2" href={`mailto:${settings.supportEmail}`}>
                  {settings.supportEmail}
                </a>
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <Link href="/login" className={buttonStyles("primary")}>
            Страница входа
          </Link>
          <a href="/maintenance" className={buttonStyles("secondary")}>
            Обновить страницу
          </a>
        </div>
      </div>
    </main>
  );
}
