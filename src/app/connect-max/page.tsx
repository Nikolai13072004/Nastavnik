import { requireSession } from "@/lib/auth-guards";
import { MaxInvite } from "./max-invite";

export default async function ConnectMaxPage() {
  await requireSession();
  return (
    <main className="mx-auto max-w-lg px-6 py-12">
      <h1 className="text-2xl font-semibold">Подключить свой MAX</h1>
      <p className="mt-4 text-[var(--ink-muted)]">Получите одноразовый код и введите его в мини-приложении компании в MAX. Код связывает только вашу текущую учётную запись LMS.</p>
      <MaxInvite />
    </main>
  );
}
