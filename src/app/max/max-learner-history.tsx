"use client";

import { useEffect, useState } from "react";
import type { getMaxLearnerHistory } from "@/modules/max/server/manager-learning-history";
import styles from "./max.module.css";

type History = Exclude<
  Awaited<ReturnType<typeof getMaxLearnerHistory>>,
  { error: string }
>;
const outcomeNames: Record<string, string> = {
  PASSED: "Сдано",
  FAILED: "Не сдано",
  IN_PROGRESS: "Не завершено",
  PENDING_REVIEW: "На проверке",
};
function date(value: Date | string | null) {
  return value ? new Date(value).toLocaleString("ru-RU") : "Не указан";
}

export function MaxLearnerHistory({
  token,
  learnerId,
  canAssign,
  onClose,
  onRenew,
  onSaved,
}: {
  token: string;
  learnerId: string;
  canAssign: boolean;
  onClose: () => void;
  onRenew: () => void;
  onSaved: () => void;
}) {
  const [data, setData] = useState<History | null>(null);
  const [page, setPage] = useState(0);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setLoadError("");
    void (async () => {
      try {
        const query = new URLSearchParams({ learnerId, page: String(page) });
        const response = await fetch(`/api/max/manager-history?${query}`, {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
          cache: "no-store",
        });
        if (response.status === 401) onRenew();
        if (!response.ok) throw new Error("History unavailable");
        const result: History = await response.json();
        if (!controller.signal.aborted) setData(result);
      } catch {
        if (!controller.signal.aborted)
          setLoadError(
            "Не удалось загрузить историю. Попробуйте обновить карточку.",
          );
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    })();
    return () => controller.abort();
  }, [learnerId, token, page, refresh, onRenew]);

  async function saveDeadline(
    event: React.FormEvent<HTMLFormElement>,
    courseId: string,
  ) {
    event.preventDefault();
    if (saving) return;
    const fields = new FormData(event.currentTarget);
    const value = String(fields.get("dueAt") ?? "");
    setSaving(true);
    setMessage("");
    try {
      const dueAt = value ? new Date(value).toISOString() : null;
      const response = await fetch("/api/max/study-plan", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          courseId,
          learnerId,
          dueAt,
          remindersEnabled: fields.get("reminders") === "on",
        }),
        signal: AbortSignal.timeout(15000),
        cache: "no-store",
      });
      if (response.status === 401) onRenew();
      if (!response.ok) throw new Error("Deadline unavailable");
      setMessage(
        "Срок сохранён. Доступ к курсу и прежние результаты не изменились.",
      );
      setRefresh((value) => value + 1);
      onSaved();
    } catch {
      setMessage(
        "Не удалось сохранить срок. Выберите будущую дату не более чем через год и повторите.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.learnerHistory} aria-label="Карточка сотрудника">
      <div className={styles.documentActions}>
        <button type="button" className={styles.back} onClick={onClose}>
          Закрыть карточку
        </button>
        <button
          type="button"
          className={styles.back}
          disabled={busy}
          onClick={() => setRefresh((value) => value + 1)}
        >
          Обновить
        </button>
      </div>
      {busy && <p role="status">Загружаем историю...</p>}
      {loadError && <p role="alert">{loadError}</p>}
      {message && <p role="status">{message}</p>}
      {data && (
        <>
          <h3>{data.learner.name}</h3>
          {data.learner.status !== "ACTIVE" && (
            <p>Профиль в архиве. Показана сохранённая история.</p>
          )}
          <h4>Курсы и материалы</h4>
          {!data.courses.length && <p>Пока нет назначений и результатов.</p>}
          {data.coursesTruncated && <p>Показаны первые 100 курсов.</p>}
          {data.courses.map((course) => (
            <details key={course.id} className={styles.sourceDetails}>
              <summary>{course.title}</summary>
              <p>
                {course.access === "ACTIVE"
                  ? "Доступ открыт"
                  : course.access === "EXPIRED"
                    ? "Срок доступа истёк"
                    : "Назначение снято"}
              </p>
              <p>Срок обучения: {date(course.plan?.dueAt ?? null)}</p>
              <ul>
                {course.items
                  .filter((item) => item.type !== "QUIZ")
                  .map((item) => (
                    <li key={item.id}>
                      {item.title}: {item.views[0]?.progressPercent ?? 0}%
                      {item.views[0] && ` · ${date(item.views[0].viewedAt)}`}
                    </li>
                  ))}
              </ul>
              {course.certificates.map((certificate) => (
                <p key={certificate.serial}>
                  Сертификат:{" "}
                  {certificate.status === "ISSUED" ? "выдан" : "отозван"},{" "}
                  {date(certificate.issuedAt)}
                </p>
              ))}
              {canAssign &&
                course.access === "ACTIVE" &&
                course.status === "PUBLISHED" &&
                data.learner.status === "ACTIVE" && (
                  <form
                    key={`${course.id}-${course.plan?.dueAt ?? ""}-${course.plan?.remindersEnabled ?? false}`}
                    className={styles.feedbackForm}
                    onSubmit={(event) => void saveDeadline(event, course.id)}
                  >
                    <label>
                      Пройти до
                      <input
                        type="datetime-local"
                        name="dueAt"
                        defaultValue={
                          course.plan?.dueAt
                            ? new Date(
                                new Date(course.plan.dueAt).getTime() -
                                  new Date().getTimezoneOffset() * 60000,
                              )
                                .toISOString()
                                .slice(0, 16)
                            : ""
                        }
                      />
                    </label>
                    <label className={styles.checkLabel}>
                      <input
                        type="checkbox"
                        name="reminders"
                        defaultChecked={course.plan?.remindersEnabled}
                      />
                      Напоминать в MAX, не чаще одного раза в сутки
                    </label>
                    <p>
                      За 3 дня до срока и после просрочки, пока обучение не
                      завершено. Пустая дата снимает срок и напоминания.
                    </p>
                    <button className={styles.retry} disabled={saving}>
                      {saving ? "Сохраняем..." : "Сохранить срок"}
                    </button>
                  </form>
                )}
            </details>
          ))}
          <h4>Попытки тестов: {data.totalAttempts}</h4>
          {!data.attempts.length && <p>Попыток пока нет.</p>}
          {data.attempts.map((attempt) => (
            <details key={attempt.id} className={styles.sourceDetails}>
              <summary>
                {attempt.quizTitle} · попытка {attempt.attemptNumber} ·{" "}
                {outcomeNames[attempt.outcome] ?? attempt.outcome}
              </summary>
              <p>{attempt.courseTitle}</p>
              <p>
                Начата: {date(attempt.createdAt)}
                {attempt.outcome !== "IN_PROGRESS" &&
                  ` · завершена: ${date(attempt.completedAt)}`}
              </p>
              <p>
                Баллы: {attempt.score}/{attempt.maxScore} · верных ответов:{" "}
                {attempt.correctAnswers}/{attempt.totalQuestions}
              </p>
              <ol>
                {attempt.questions.map((question) => (
                  <li key={question.id}>
                    <strong>{question.prompt}</strong>
                    <p>Ответ: {question.selected}</p>
                    {question.correct && (
                      <p>Правильный вариант: {question.correct}</p>
                    )}
                    <p>
                      {question.isCorrect === null
                        ? "Не оценено автоматически"
                        : question.isCorrect
                          ? "Верно"
                          : "Ошибка"}
                    </p>
                  </li>
                ))}
              </ol>
            </details>
          ))}
          <div className={styles.documentActions}>
            <button
              type="button"
              className={styles.back}
              disabled={busy || page === 0}
              onClick={() => setPage((value) => value - 1)}
            >
              Предыдущие
            </button>
            <span>Страница {page + 1}</span>
            <button
              type="button"
              className={styles.back}
              disabled={busy || (page + 1) * 20 >= data.totalAttempts}
              onClick={() => setPage((value) => value + 1)}
            >
              Следующие
            </button>
          </div>
          <h4>Редакции документов</h4>
          {!data.documents.length && (
            <p>Повторное обучение пока не назначалось.</p>
          )}
          {data.documentsTruncated && (
            <p>Показаны последние 200 назначений документов.</p>
          )}
          <ul>
            {data.documents.map((training) => (
              <li key={training.document.id}>
                {training.document.title}, редакция{" "}
                {training.document.versionNumber}
                <p>
                  {training.document.revokedAt
                    ? "Прежняя редакция"
                    : "Действующая редакция"}{" "}
                  ·
                  {training.passedAt
                    ? ` проверка пройдена ${date(training.passedAt)}`
                    : training.viewedAt
                      ? " прочитано, проверка не пройдена"
                      : " не открыто"}
                  {` · попыток: ${training.attempts}`}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
