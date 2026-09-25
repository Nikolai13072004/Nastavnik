"use client";

import { useEffect, useState } from "react";
import styles from "./max.module.css";

type DocumentRow = {
  id: string;
  title: string;
  sourceName: string;
  versionNumber: number;
  changeSummary: string | null;
};
type Document = DocumentRow & {
  contentText: string;
  checkQuestion: string | null;
  checkOptions: string[] | null;
  training: { viewedAt: string | null; passedAt: string | null; attempts: number } | null;
};

export function MaxCourseDocuments({ courseId, token, onRenew }: {
  courseId: string;
  token: string;
  onRenew: () => void;
}) {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [opened, setOpened] = useState<Document | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [answerIndex, setAnswerIndex] = useState<number | null>(null);
  const [answerMessage, setAnswerMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function load() {
      try {
        const query = new URLSearchParams({ courseId });
        const response = await fetch(`/api/max/documents?${query}`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) { if (active) onRenew(); return; }
        if (!response.ok) throw new Error("Could not list documents");
        const result: { documents: DocumentRow[] } = await response.json();
        if (active) setDocuments(result.documents);
      } catch {
        if (active) setMessage("Документы временно недоступны.");
      }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [courseId, token, onRenew, refreshKey]);

  async function openDocument(documentId: string) {
    setBusy(true);
    setMessage("");
    try {
      const query = new URLSearchParams({ courseId, documentId });
      const response = await fetch(`/api/max/documents?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (response.status === 401) { onRenew(); return; }
      if (!response.ok) {
        setMessage(response.status === 403 || response.status === 404
          ? "Доступ к документу изменился. Обновите курс."
          : "Не удалось открыть документ. Попробуйте снова.");
        return;
      }
      const result: { document: Document } = await response.json();
      setOpened(result.document);
      setAnswerIndex(null);
      setAnswerMessage("");
      if (result.document.training && !result.document.training.viewedAt) {
        const view = await fetch("/api/max/document-training", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({ courseId, documentId, action: "view" }),
          cache: "no-store",
        });
        if (view.status === 401) { onRenew(); return; }
        if (!view.ok) {
          setMessage("Не удалось сохранить открытие документа. Попробуйте открыть его снова.");
          return;
        }
        setOpened({ ...result.document, training: { ...result.document.training, viewedAt: new Date().toISOString() } });
      }
    } catch {
      setMessage("Не удалось открыть документ. Проверьте соединение.");
    } finally {
      setBusy(false);
    }
  }

  async function submitAnswer() {
    if (!opened || answerIndex === null || busy) return;
    setBusy(true);
    setAnswerMessage("");
    try {
      const response = await fetch("/api/max/document-training", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ courseId, documentId: opened.id, action: "answer", answerIndex }),
        cache: "no-store",
      });
      if (response.status === 401) { onRenew(); return; }
      if (!response.ok) throw new Error("Could not submit answer");
      const result: { status: string; attempts?: number } = await response.json();
      setAnswerMessage(result.status === "PASSED" ? "Верно. Новая редакция изучена."
        : result.status === "EXHAUSTED" ? "Попытки закончились. Обратитесь к HR."
          : result.status === "INCORRECT" ? "Пока неверно. Перечитайте изменение и попробуйте ещё раз."
            : "Сначала откройте документ заново.");
      setOpened((current) => current && current.training ? {
        ...current,
        training: {
          ...current.training,
          attempts: result.attempts ?? current.training.attempts,
          passedAt: result.status === "PASSED" ? new Date().toISOString() : current.training.passedAt,
        },
      } : current);
    } catch {
      setAnswerMessage("Ответ не сохранился. Проверьте соединение и попробуйте снова.");
    } finally {
      setBusy(false);
    }
  }

  if (documents.length === 0 && !message) return null;
  return <section className={styles.workDocuments} aria-labelledby="max-course-documents-title">
    <h4 id="max-course-documents-title">Рабочие документы</h4>
    <button type="button" className={styles.back} disabled={busy} onClick={() => {
      setOpened(null);
      setMessage("");
      setRefreshKey((value) => value + 1);
    }}>Обновить документы</button>
    {documents.length > 0 && <ul className={styles.documentList}>{documents.map((document) => <li key={document.id}>
      <div><strong>{document.title}</strong><small>Версия {document.versionNumber} · {document.sourceName}</small>
        {document.changeSummary && <p>Изменилось: {document.changeSummary}</p>}</div>
      <button type="button" className={styles.back} disabled={busy}
        onClick={() => void openDocument(document.id)}>Читать</button>
    </li>)}</ul>}
    {message && <p role="status">{message}</p>}
    {opened && <div className={styles.documentPreview}>
      <h5>{opened.title}</h5>
      {opened.changeSummary && <p>Что изменилось: {opened.changeSummary}</p>}
      <pre>{opened.contentText}</pre>
      {opened.training && opened.checkQuestion && opened.checkOptions && <div className={styles.documentTraining}>
        <h5>Проверьте, что вы поняли изменение</h5>
        <p>{opened.checkQuestion}</p>
        {opened.training.passedAt ? <p role="status">Проверка пройдена.</p>
          : opened.training.attempts >= 3 ? <p role="status">Попытки закончились. Обратитесь к HR.</p>
            : <>
              <fieldset className={styles.quizQuestion}>
                <legend>Выберите один ответ</legend>
                {opened.checkOptions.map((option, index) => <label key={index}>
                  <input type="radio" name={`document-${opened.id}`} checked={answerIndex === index}
                    onChange={() => setAnswerIndex(index)} />
                  {option}
                </label>)}
              </fieldset>
              <button type="button" className={styles.retry} disabled={busy || answerIndex === null ||
                !opened.training.viewedAt} onClick={() => void submitAnswer()}>Ответить</button>
            </>}
        {answerMessage && <p role="status">{answerMessage}</p>}
      </div>}
      <button type="button" className={styles.back} onClick={() => setOpened(null)}>Закрыть текст</button>
    </div>}
  </section>;
}
