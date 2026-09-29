"use client";

import { useEffect, useState } from "react";
import type { getMaxQuestionErrors } from "@/modules/max/server/manager-learning-history";
import type { getMaxAiQuality } from "@/modules/max/server/ai-quality";
import styles from "./max.module.css";

type Errors = Exclude<
  Awaited<ReturnType<typeof getMaxQuestionErrors>>,
  { error: string }
>;
type Quality = Exclude<
  Awaited<ReturnType<typeof getMaxAiQuality>>,
  { error: string }
>;
const verdictNames: Record<string, string> = {
  PENDING: "Не рассмотрено",
  CORRECT: "Ответ верный",
  INCORRECT: "Ошибка подтверждена",
  INCONCLUSIVE: "Недостаточно данных",
};
const reasonNames: Record<string, string> = {
  WRONG_ANSWER: "Неверный ответ",
  WRONG_SOURCE: "Неверный источник",
  MISSING_ANSWER: "AI не нашёл ответ",
};

export function MaxHrAnalytics({
  courseId,
  token,
  onRenew,
}: {
  courseId: string;
  token: string;
  onRenew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [errors, setErrors] = useState<Errors | null>(null);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setBusy(true);
    setMessage("");
    void (async () => {
      try {
        const query = new URLSearchParams({ courseId });
        const results = await Promise.all(
          ["manager-history", "ai-quality"].map((path) =>
            fetch(`/api/max/${path}?${query}`, {
              headers: { Authorization: `Bearer ${token}` },
              signal: controller.signal,
              cache: "no-store",
            }),
          ),
        );
        if (results.some((response) => response.status === 401)) onRenew();
        if (results.some((response) => !response.ok))
          throw new Error("Analytics unavailable");
        const [errorData, qualityData] = await Promise.all(
          results.map((response) => response.json()),
        );
        if (!controller.signal.aborted) {
          setErrors(errorData);
          setQuality(qualityData);
        }
      } catch {
        if (!controller.signal.aborted)
          setMessage("Не удалось загрузить статистику. Попробуйте обновить.");
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    })();
    return () => controller.abort();
  }, [courseId, token, open, refresh, onRenew]);

  async function review(
    event: React.FormEvent<HTMLFormElement>,
    eventId: string,
  ) {
    event.preventDefault();
    if (saving) return;
    const fields = new FormData(event.currentTarget);
    setSaving(true);
    setMessage("");
    try {
      const response = await fetch("/api/max/ai-quality", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          courseId,
          eventId,
          verdict: fields.get("verdict"),
          comment: fields.get("comment") ?? "",
        }),
        signal: AbortSignal.timeout(15000),
        cache: "no-store",
      });
      if (response.status === 401) onRenew();
      if (!response.ok) throw new Error("Review unavailable");
      setRefresh((value) => value + 1);
    } catch {
      setMessage(
        "Не удалось сохранить проверку. Обновите статистику: сообщение мог уже рассмотреть другой HR.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.hrAnalytics}>
      <button
        type="button"
        className={styles.back}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "Скрыть аналитику" : "Ошибки в тестах и качество AI"}
      </button>
      {open && (
        <>
          <button
            type="button"
            className={styles.back}
            disabled={busy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            Обновить статистику
          </button>
          {busy && <p role="status">Загружаем статистику...</p>}
          {message && <p role="status">{message}</p>}
          {errors && (
            <>
              <h4>Частые ошибки в тестах</h4>
              <p>
                Завершённых попыток проанализировано: {errors.attemptsAnalyzed}.
                Повторы одного сотрудника учитываются отдельно.
              </p>
              {errors.truncated && (
                <p>Расчёт по последним 2000 завершённым попыткам.</p>
              )}
              {errors.questionsTruncated && (
                <p>Показаны 100 вопросов с наибольшим числом ошибок.</p>
              )}
              {!errors.questions.length && (
                <p>Пока нет завершённых попыток с выбором ответа.</p>
              )}
              <ol>
                {errors.questions.map((question, index) => (
                  <li key={index}>
                    <strong>{question.prompt}</strong>
                    <p>
                      Ошибок: {question.incorrect} из {question.answered}{" "}
                      ответов (
                      {Math.round(
                        (question.incorrect / question.answered) * 100,
                      )}
                      %).
                    </p>
                    <p>Верный вариант: {question.correct}</p>
                  </li>
                ))}
              </ol>
            </>
          )}
          {quality && (
            <>
              <h4>Качество AI за {quality.periodDays} дней</h4>
              <p>
                Запросов: {quality.total} · ответов: {quality.answered} ·
                отказов: {quality.refused} · сбоев: {quality.errors}.
              </p>
              {quality.total === 0 ? (
                <p>
                  Данных пока нет. Статистика начнёт собираться после
                  обновления.
                </p>
              ) : (
                <p>
                  Среднее время: {((quality.averageMs ?? 0) / 1000).toFixed(1)}{" "}
                  с.
                  {quality.p95Ms !== null &&
                    ` 95% из последних ${quality.timingSampleCount} запросов обработаны не дольше ${(quality.p95Ms / 1000).toFixed(1)} с.`}{" "}
                  Это время серверной обработки, не доставки сообщения MAX.
                </p>
              )}
              <p>
                Жалоб: {quality.feedbackCount}.{" "}
                {quality.verdicts
                  .map(
                    (item) =>
                      `${verdictNames[item.verdict] ?? item.verdict}: ${item.count}`,
                  )
                  .join(" · ")}
              </p>
              <p>
                Жалобы не измеряют точность всей модели. Отказ может быть
                правильным. Для оценки точности нужен отдельный набор вопросов с
                проверенными ответами.
              </p>
              <h5>Сообщения сотрудников</h5>
              <p>
                Здесь только ответы, которые сотрудник явно отправил HR.
                Остальная переписка недоступна.
              </p>
              {quality.feedbackTruncated && (
                <p>Показаны последние 50 сообщений.</p>
              )}
              {!quality.feedback.length && (
                <p>Сообщений об ошибках пока нет.</p>
              )}
              {quality.feedback.map((item) => (
                <details key={item.eventId} className={styles.sourceDetails}>
                  <summary>
                    {reasonNames[item.reason] ?? item.reason} ·{" "}
                    {verdictNames[item.verdict]}
                  </summary>
                  <p>{new Date(item.createdAt).toLocaleString("ru-RU")}</p>
                  <strong>Вопрос</strong>
                  <p>{item.question}</p>
                  <strong>Ответ AI</strong>
                  <p className={styles.preservedText}>{item.answer}</p>
                  {item.comment && <p>Пояснение сотрудника: {item.comment}</p>}
                  <details>
                    <summary>Источники ответа</summary>
                    <p className={styles.preservedText}>
                      {JSON.parse(item.sourcesJson)
                        .map(
                          (source: { title: string; snippet: string }) =>
                            `${source.title}\n${source.snippet}`,
                        )
                        .join("\n\n") || "Источников нет"}
                    </p>
                  </details>
                  {item.verdict === "PENDING" ? (
                    <form
                      className={styles.feedbackForm}
                      onSubmit={(event) => void review(event, item.eventId)}
                    >
                      <label>
                        Результат проверки
                        <select name="verdict" required defaultValue="">
                          <option value="" disabled>
                            Выберите результат
                          </option>
                          <option value="CORRECT">Ответ верный</option>
                          <option value="INCORRECT">Ошибка подтверждена</option>
                          <option value="INCONCLUSIVE">
                            Недостаточно данных
                          </option>
                        </select>
                      </label>
                      <label>
                        Комментарий HR
                        <textarea name="comment" maxLength={1000} rows={2} />
                      </label>
                      <button className={styles.retry} disabled={saving}>
                        Сохранить проверку
                      </button>
                    </form>
                  ) : (
                    <p>
                      Проверено{" "}
                      {item.reviewedAt &&
                        new Date(item.reviewedAt).toLocaleString("ru-RU")}
                      . {item.reviewComment}
                    </p>
                  )}
                </details>
              ))}
            </>
          )}
        </>
      )}
    </section>
  );
}
