"use client";

import { useState } from "react";

const errors: Record<string, string> = {
  ALREADY_LINKED: "Этот сотрудник уже связан с MAX. Для смены профиля обратитесь к администратору.",
  UNAVAILABLE: "Нужна активная учётная запись с назначенной организацией. Обратитесь к HR.",
};

export function MaxInvite() {
  const [invite, setInvite] = useState<{ token: string; expiresAt: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function issue() {
    setBusy(true);
    setError("");
    setInvite(null);
    try {
      const response = await fetch("/api/max/invitation", { method: "POST", signal: AbortSignal.timeout(10_000) });
      if (response.redirected || response.status === 401) {
        setError("Сессия завершена. Войдите в LMS заново.");
        return;
      }
      const data = await response.json();
      if (!response.ok) {
        setError(errors[data.error] ?? "Не удалось создать код. Попробуйте позже.");
        return;
      }
      setInvite(data);
    } catch {
      setError("Нет ответа от сервера. Проверьте соединение и повторите попытку.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-8">
      <button type="button" disabled={busy} onClick={() => void issue()}
        className="min-h-11 rounded-md border border-[var(--accent)] px-4 py-2 font-medium text-[var(--accent)] disabled:opacity-50">
        {busy ? "Создаём код…" : invite ? "Создать новый код" : "Получить код"}
      </button>
      {error && <p role="alert" className="mt-4 text-[var(--danger)]">{error}</p>}
      {invite && <div className="mt-6" role="status">
        <label htmlFor="max-invite" className="block text-sm font-medium">Одноразовый код</label>
        <input id="max-invite" readOnly value={invite.token} onFocus={(event) => event.target.select()}
          className="mt-2 w-full rounded-md border border-[var(--line)] bg-[var(--surface)] p-3 font-mono text-sm" />
        <p className="mt-3 text-sm text-[var(--ink-muted)]">Действует до {new Date(invite.expiresAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}. Новый код отменяет предыдущий. Не передавайте его другим людям.</p>
      </div>}
    </section>
  );
}
