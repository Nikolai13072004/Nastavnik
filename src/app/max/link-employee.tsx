"use client";

import { useState, type FormEvent } from "react";
import { Button, Input, Typography } from "@maxhub/max-ui";
import { Link2 } from "lucide-react";
import styles from "./max.module.css";

const errorMessages: Record<string, string> = {
  INVALID_INVITE: "Код недействителен или истёк. Запросите новый код привязки.",
  ALREADY_LINKED: "Этот профиль MAX или сотрудник уже связан с другой учётной записью. Обратитесь к администратору.",
  INVALID_LAUNCH: "Данные входа устарели. Закройте мини-приложение и откройте его снова.",
};

export function LinkEmployee({ onLinked }: { onLinked: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const bridge = (window as Window & { WebApp?: { initData?: string } }).WebApp;
      const response = await fetch("/api/max/identity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData: bridge?.initData ?? "", inviteToken: token.trim() }),
        signal: AbortSignal.timeout(10_000),
      });
      const result = await response.json();
      if (!response.ok) {
        setError(errorMessages[result.error] ?? "Не удалось подтвердить привязку. Повторите проверку профиля перед новой попыткой: запрос мог уже выполниться.");
        return;
      }
      setToken("");
      onLinked();
    } catch {
      setError("Нет ответа от сервера. Откройте приложение повторно: привязка могла уже сохраниться.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className={styles.linkForm}>
      <Typography.Title asChild variant="large-strong"><label htmlFor="employee-code">Одноразовый код привязки</label></Typography.Title>
      <Input id="employee-code" value={token} onChange={(event) => setToken(event.target.value)}
        required maxLength={32} minLength={32} pattern="[a-zA-Z0-9_\-]{32}" autoComplete="off"
        autoCapitalize="none" spellCheck={false} disabled={busy} aria-describedby="employee-code-hint"
        placeholder="Вставьте 32-значный код" iconBefore={<Link2 size={20} />} />
      <p id="employee-code-hint">Не вводите код, который прислал другой человек.</p>
      {error && <p role="alert">{error}</p>}
      <Button type="submit" stretched size="medium" loading={busy} disabled={busy || token.length !== 32}>
        Связать профиль
      </Button>
    </form>
  );
}
