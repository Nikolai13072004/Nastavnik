"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./max.module.css";
import { MaxDocumentText } from "./max-document-text";
import { readableDocumentText } from "@/lib/document-text";

type Source = {
  documentId: string;
  courseDocumentId?: string;
  title: string;
  snippet: string;
};
type Answer = { answer: string; refused: boolean; sources: Source[] };
type SourceDocument = {
  id: string;
  title: string;
  versionNumber: number;
  contentText: string;
};

export function MaxCourseKnowledge({
  courseId,
  token,
  onRenew,
}: {
  courseId: string;
  token: string;
  onRenew: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [sourceDocument, setSourceDocument] = useState<SourceDocument | null>(
    null,
  );
  const [sourceMessage, setSourceMessage] = useState("");
  const [sourceBusy, setSourceBusy] = useState(false);
  const sourceRequest = useRef<AbortController | null>(null);
  const preview = useRef<HTMLDivElement | null>(null);
  const sourceButton = useRef<HTMLButtonElement | null>(null);

  useEffect(() => () => sourceRequest.current?.abort(), []);

  useEffect(() => {
    if (!sourceDocument || !preview.current) return;
    preview.current.focus({ preventScroll: true });
    preview.current.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "start",
    });
  }, [sourceDocument]);

  async function openSource(documentId: string) {
    sourceRequest.current?.abort();
    const controller = new AbortController();
    sourceRequest.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    setSourceBusy(true);
    setSourceDocument(null);
    setSourceMessage("");
    try {
      const query = new URLSearchParams({ courseId, documentId });
      const response = await fetch(`/api/max/documents?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
        cache: "no-store",
      });
      if (sourceRequest.current !== controller) return;
      if (response.status === 401) {
        onRenew();
        return;
      }
      if (!response.ok) throw new Error("Source is unavailable");
      const result: { document: SourceDocument } = await response.json();
      if (sourceRequest.current === controller)
        setSourceDocument(result.document);
    } catch {
      if (sourceRequest.current === controller) {
        setSourceMessage(
          "Не удалось открыть источник. Возможно, доступ или редакция документа изменились. Попробуйте снова.",
        );
      }
    } finally {
      window.clearTimeout(timeout);
      if (sourceRequest.current === controller) setSourceBusy(false);
    }
  }

  function downloadSource() {
    if (!sourceDocument) return;
    const blob = new Blob([sourceDocument.contentText], {
      type: "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `document-${sourceDocument.id}-v${sourceDocument.versionNumber}.txt`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  async function ask(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || question.trim().length < 3) return;
    setBusy(true);
    setAnswer(null);
    setMessage("");
    sourceRequest.current?.abort();
    sourceRequest.current = null;
    setSourceDocument(null);
    setSourceMessage("");
    setSourceBusy(false);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 115_000);
    try {
      const response = await fetch("/api/max/knowledge", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
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
      setMessage(
        "Не удалось получить ответ. Попробуйте снова или обратитесь к HR.",
      );
    } finally {
      window.clearTimeout(timeout);
      setBusy(false);
    }
  }

  return (
    <section
      className={styles.courseSearch}
      aria-labelledby="course-knowledge-title"
    >
      <h4 id="course-knowledge-title">Вопрос по документу</h4>
      <p>
        AI отвечает по утверждённому документу курса. Проверяйте ответ по
        источнику.
      </p>
      <form onSubmit={(event) => void ask(event)}>
        <label htmlFor="knowledge-question">Ваш вопрос</label>
        <textarea
          id="knowledge-question"
          value={question}
          maxLength={500}
          rows={3}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Например: что делать, если код потерян?"
        />
        <button
          type="submit"
          className={styles.retry}
          disabled={busy || question.trim().length < 3}
        >
          {busy ? "Готовим ответ…" : "Спросить AI"}
        </button>
      </form>
      {answer && (
        <div className={styles.searchResult} role="status">
          <MaxDocumentText text={answer.answer} />
          {!answer.refused &&
            answer.sources.slice(0, 3).map((source, index) => (
              <details
                className={styles.sourceDetails}
                key={`${source.documentId}-${index}`}
              >
                <summary>
                  Источник {index + 1}: {source.title}
                </summary>
                <p>{readableDocumentText(source.snippet).slice(0, 400)}</p>
                {source.courseDocumentId ? (
                  <button
                    type="button"
                    className={styles.sourceLink}
                    disabled={sourceBusy}
                    onClick={(event) => {
                      sourceButton.current = event.currentTarget;
                      void openSource(source.courseDocumentId!);
                    }}
                  >
                    Читать документ
                  </button>
                ) : (
                  <p>
                    Полный документ пока недоступен. Уточните источник у HR.
                  </p>
                )}
              </details>
            ))}
        </div>
      )}
      {sourceBusy && <p role="status">Открываем источник...</p>}
      {sourceMessage && <p role="status">{sourceMessage}</p>}
      {sourceDocument && (
        <div
          ref={preview}
          tabIndex={-1}
          className={styles.documentPreview}
          role="region"
          aria-label={`Источник: ${sourceDocument.title}`}
        >
          <h5>{sourceDocument.title}</h5>
          <p>Редакция {sourceDocument.versionNumber}</p>
          <MaxDocumentText text={sourceDocument.contentText} />
          <div className={styles.documentActions}>
            <button
              type="button"
              className={styles.back}
              onClick={downloadSource}
            >
              Скачать текст (.txt)
            </button>
            <button
              type="button"
              className={styles.back}
              onClick={() => {
                setSourceDocument(null);
                sourceButton.current?.focus({ preventScroll: true });
                sourceButton.current?.scrollIntoView({
                  behavior: window.matchMedia(
                    "(prefers-reduced-motion: reduce)",
                  ).matches
                    ? "instant"
                    : "smooth",
                  block: "center",
                });
              }}
            >
              Закрыть источник
            </button>
          </div>
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
