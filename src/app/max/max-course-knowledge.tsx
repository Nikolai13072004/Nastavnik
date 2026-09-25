"use client";

import { useState } from "react";
import styles from "./max.module.css";

type Source = { documentId: string; title: string; snippet: string };
type Answer = { answer: string; refused: boolean; sources: Source[] };

export function MaxCourseKnowledge({ courseId, token, onRenew }: {
  courseId: string;
  token: string;
  onRenew: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || question.trim().length < 3) return;
    setBusy(true);
    setAnswer(null);
    setMessage("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 115_000);
    try {
      const response = await fetch("/api/max/knowledge", {
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
      if (response.status === 403) {
        setMessage("Для этого курса пока нет подтверждённых источников AI.");
        return;
      }
      if (!response.ok) throw new Error("AI request failed");
      const result: Answer = await response.json();
      setAnswer(result);
    } catch {
      setMessage("Не удалось получить ответ. Попробуйте снова или обратитесь к HR.");
    } finally {
      window.clearTimeout(timeout);
      setBusy(false);
    }
  }

  return <section className={styles.courseSearch} aria-labelledby="course-knowledge-title">
    <h4 id="course-knowledge-title">Вопрос по документу</h4>
    <p>AI отвечает по утверждённому документу курса. Проверяйте ответ по источнику.</p>
    <form onSubmit={(event) => void ask(event)}>
      <label htmlFor="knowledge-question">Ваш вопрос</label>
      <textarea id="knowledge-question" value={question} maxLength={500} rows={3}
        onChange={(event) => setQuestion(event.target.value)} placeholder="Например: что делать, если код потерян?" />
      <button type="submit" className={styles.retry} disabled={busy || question.trim().length < 3}>
        {busy ? "Готовим ответ…" : "Спросить AI"}
      </button>
    </form>
    {answer && <div className={styles.searchResult} role="status">
      <p>{answer.answer}</p>
      {!answer.refused && answer.sources.slice(0, 3).map((source, index) =>
        <small key={`${source.documentId}-${index}`}>
          Источник: {source.title}. {source.snippet.slice(0, 400)}
        </small>)}
    </div>}
    {message && <p role="status">{message}</p>}
  </section>;
}
