"use client";

import { useState } from "react";
import type { KnowledgeAnswer } from "@/modules/max/application/verify-knowledge-answer";
import styles from "./max.module.css";

export function MaxAnswerFeedback({
  token,
  courseId,
  question,
  result,
  onRenew,
}: {
  token: string;
  courseId: string;
  question: string;
  result: KnowledgeAnswer;
  onRenew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("WRONG_ANSWER");
  const [comment, setComment] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [message, setMessage] = useState("");
  if (!result.eventId) return null;

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !consent) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/max/ai-feedback", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          courseId,
          eventId: result.eventId,
          question,
          result,
          reason,
          comment,
          consent,
        }),
        signal: AbortSignal.timeout(15000),
        cache: "no-store",
      });
      if (response.status === 401) onRenew();
      if (!response.ok) throw new Error("Feedback unavailable");
      setSent(true);
      setOpen(false);
      setMessage("Сообщение передано HR. Спасибо за помощь в проверке ответа.");
    } catch {
      setMessage(
        "Не удалось передать сообщение. Попробуйте снова. Жалобу можно отправить в течение 7 дней после ответа.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.feedbackForm}>
      {!sent && !open && (
        <button
          type="button"
          className={styles.back}
          onClick={() => setOpen(true)}
        >
          Сообщить об ошибке AI
        </button>
      )}
      {open && (
        <form onSubmit={(event) => void send(event)}>
          <label>
            Что не так?
            <select
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            >
              <option value="WRONG_ANSWER">Неверный ответ</option>
              <option value="WRONG_SOURCE">
                Источник не подтверждает ответ
              </option>
              <option value="MISSING_ANSWER">
                Ответ есть в документе, но AI его не нашёл
              </option>
            </select>
          </label>
          <label>
            Пояснение, необязательно
            <textarea
              rows={3}
              maxLength={1000}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
          </label>
          <label className={styles.checkLabel}>
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
            Передать HR этот вопрос, ответ и источники. Остальная переписка не
            передаётся.
          </label>
          <div className={styles.documentActions}>
            <button className={styles.retry} disabled={busy || !consent}>
              {busy ? "Отправляем..." : "Отправить HR"}
            </button>
            <button
              type="button"
              className={styles.back}
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Отмена
            </button>
          </div>
        </form>
      )}
      {message && <p role="status">{message}</p>}
    </div>
  );
}
