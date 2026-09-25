"use client";

import { useState } from "react";
import styles from "./max.module.css";

type Passage = { materialId: string; materialTitle: string; excerpt: string };

export function MaxCourseSearch({ courseId, token, onRenew }: {
  courseId: string;
  token: string;
  onRenew: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [passage, setPassage] = useState<Passage | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function search(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || question.trim().length < 3) return;
    setBusy(true);
    setPassage(null);
    setMessage("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch("/api/max/course-search", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ courseId, question: question.trim() }),
        signal: controller.signal,
        cache: "no-store",
      });
      if (response.status === 401) {
        setMessage("Сессия истекла. Проверьте вход повторно.");
        onRenew();
        return;
      }
      if (!response.ok) throw new Error("Search failed");
      const result: { passage: Passage | null } = await response.json();
      setPassage(result.passage);
      if (!result.passage) setMessage("В доступных материалах этого курса подходящий фрагмент не найден.");
    } catch {
      setMessage("Не удалось выполнить поиск. Проверьте соединение и попробуйте снова.");
    } finally {
      window.clearTimeout(timeout);
      setBusy(false);
    }
  }

  return <section className={styles.courseSearch} aria-labelledby="course-search-title">
    <h4 id="course-search-title">Поиск по материалам курса</h4>
    <p>Сейчас это поиск цитат, не AI-ответ. Источник — только опубликованные материалы назначенного вам курса.</p>
    <form onSubmit={(event) => void search(event)}>
      <label htmlFor="course-question">Ваш вопрос</label>
      <textarea id="course-question" value={question} maxLength={500} rows={3}
        onChange={(event) => setQuestion(event.target.value)} placeholder="Например: кто утверждает изменения?" />
      <button type="submit" className={styles.retry} disabled={busy || question.trim().length < 3}>
        {busy ? "Ищем…" : "Найти в курсе"}
      </button>
    </form>
    {passage && <div className={styles.searchResult} role="status">
      <p>{passage.excerpt}</p>
      <small>Источник: {passage.materialTitle}</small>
    </div>}
    {message && <p role="status">{message}</p>}
  </section>;
}
