"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import styles from "./max.module.css";
import { MaxDocumentText } from "./max-document-text";
import { MaxDocumentDownload } from "./max-document-download";

type DocumentRow = {
  id: string;
  title: string;
  sourceName: string;
  approvedAt: string | null;
  revokedAt: string | null;
  supersedesId: string | null;
  versionNumber: number;
  changeSummary: string | null;
  aiConnected: boolean;
  trainingRecipients: Array<{
    userId: string;
    viewedAt: string | null;
    passedAt: string | null;
    attempts: number;
    user: { name: string };
  }>;
};

type Preview = {
  id: string;
  title: string;
  sourceName: string;
  contentText: string;
  hasOriginal: boolean;
  changeSummary: string | null;
  checkQuestion: string | null;
  checkOptionsJson: string | null;
  checkCorrectIndex: number | null;
};
type Recipient = { id: string; name: string };

function waitForIndex(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Connection cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 3000);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

function encodeFile(bytes: Uint8Array) {
  let base64 = "";
  for (let offset = 0; offset < bytes.length; offset += 12 * 1024) {
    base64 += btoa(String.fromCharCode(...bytes.subarray(offset, offset + 12 * 1024)));
  }
  return base64;
}

export function MaxManagerDocuments({ courseId, token, onRenew }: {
  courseId: string;
  token: string;
  onRenew: () => void;
}) {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [title, setTitle] = useState("");
  const [supersedesId, setSupersedesId] = useState("");
  const [changeSummary, setChangeSummary] = useState("");
  const [checkQuestion, setCheckQuestion] = useState("");
  const [checkOptions, setCheckOptions] = useState(["", "", ""]);
  const [checkCorrectIndex, setCheckCorrectIndex] = useState(0);
  const [audience, setAudience] = useState<Recipient[] | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirmation, setConfirmation] = useState<{ id: string; publish: boolean } | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [aiConnectionEnabled, setAiConnectionEnabled] = useState(false);
  const [aiImportEnabled, setAiImportEnabled] = useState(false);
  const [connectingId, setConnectingId] = useState<string | null>(null);
  const connection = useRef<AbortController | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    setBusy(false);
    setConnectingId(null);
    return () => connection.current?.abort();
  }, [courseId, token]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    async function load() {
      try {
        const query = new URLSearchParams({ scope: "manager", courseId });
        const response = await fetch(`/api/max/documents?${query}`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Could not load documents");
        const result: { documents: DocumentRow[]; audience: Recipient[] | null;
          aiConnectionEnabled: boolean; aiImportEnabled?: boolean } = await response.json();
        if (active) {
          setDocuments(result.documents);
          setAudience(result.audience);
          setAiConnectionEnabled(result.aiConnectionEnabled);
          setAiImportEnabled(result.aiImportEnabled === true);
        }
      } catch {
        if (active) setMessage("Не удалось загрузить документы. Обновите список позже.");
      }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [courseId, token, refreshKey]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || busy) return;
    const isPdf = /\.pdf$/i.test(file.name);
    if (!/\.(txt|md|pdf)$/i.test(file.name) || file.size > (isPdf ? 512 : 32) * 1024) {
      setMessage("Подойдёт .txt или .md до 32 КБ либо текстовый PDF до 512 КБ.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const revision = supersedesId ? {
        supersedesId, changeSummary, checkQuestion, checkOptions, checkCorrectIndex,
      } : {};
      const body = JSON.stringify({ courseId, title, sourceName: file.name, ...revision,
        fileBase64: encodeFile(new Uint8Array(await file.arrayBuffer())) });
      if (new Blob([body]).size > (isPdf ? 720 : 48) * 1024) {
        setMessage("Файл слишком большой. Сократите документ и попробуйте снова.");
        return;
      }
      const response = await fetch("/api/max/documents", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body,
        cache: "no-store",
      });
      if (response.status === 401) { onRenew(); return; }
      if (!response.ok) {
        const result: { error?: string; status?: string } = await response.json().catch(() => ({}));
        if (result.error === "PDF_NO_TEXT") {
          setMessage("В PDF нет выделяемого текста. Для скана пока нужен текстовый файл.");
          return;
        }
        if (result.error === "PDF_TOO_MANY_PAGES") {
          setMessage("PDF должен содержать не больше 10 страниц.");
          return;
        }
        setMessage(response.status === 409 ? "Эта редакция уже заменяется или документ изменился. Обновите список."
          : response.status === 400 || response.status === 413
          ? "Проверьте файл: PDF до 512 КБ и 10 страниц, извлечённый текст до 32 КБ."
          : "Не удалось загрузить документ. Проверьте доступ и соединение.");
        return;
      }
      setFile(null);
      setTitle("");
      setSupersedesId("");
      setChangeSummary("");
      setCheckQuestion("");
      setCheckOptions(["", "", ""]);
      setCheckCorrectIndex(0);
      setFileInputKey((value) => value + 1);
      setMessage("Документ загружен как черновик. Проверьте извлечённый текст перед публикацией.");
      setRefreshKey((value) => value + 1);
    } catch {
      setMessage("Нет ответа от сервера. Перед повторной загрузкой обновите список документов.");
    } finally {
      setBusy(false);
    }
  }

  async function showPreview(documentId: string) {
    setBusy(true);
    setMessage("");
    try {
      const query = new URLSearchParams({ scope: "manager", courseId, documentId });
      const response = await fetch(`/api/max/documents?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (response.status === 401) { onRenew(); return; }
      if (!response.ok) throw new Error("Could not open document");
      const result: { document: Preview } = await response.json();
      setPreview(result.document);
    } catch {
      setMessage("Не удалось открыть документ. Попробуйте снова.");
    } finally {
      setBusy(false);
    }
  }

  async function changePublication(documentId: string, publish: boolean) {
    setBusy(true);
    setMessage("");
    try {
      const selected = documents.find((document) => document.id === documentId);
      const response = await fetch("/api/max/documents", {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ courseId, documentId, publish,
          ...(publish && documents.find((document) => document.id === documentId)?.supersedesId
            ? { expectedRecipientIds: audience?.map(({ id }) => id) ?? [] } : {}) }),
        cache: "no-store",
      });
      if (response.status === 401) { onRenew(); return; }
      if (!response.ok) {
        const result: { status?: string } = await response.json().catch(() => ({}));
        setMessage(result.status === "AUDIENCE_CHANGED"
          ? "Список получателей изменился. Обновите документы и подтвердите заново."
          : result.status === "AUDIENCE_TOO_LARGE"
            ? "В пилоте можно назначить повторное обучение не более чем 200 сотрудникам."
            : "Не удалось изменить публикацию. Обновите список и попробуйте снова.");
        setRefreshKey((value) => value + 1);
        return;
      }
      setPreview(null);
      setConfirmation(null);
      setMessage(publish ? selected?.supersedesId
        ? "Новая редакция опубликована. Проверка доступна сотрудникам в MAX."
        : "Документ опубликован для сотрудников с доступом к курсу."
        : selected?.supersedesId && !selected.approvedAt ? "Черновик удалён. Можно загрузить новую редакцию."
          : "Документ снят с публикации.");
      setRefreshKey((value) => value + 1);
    } catch {
      setMessage("Не удалось изменить публикацию. Обновите список и попробуйте снова.");
    } finally {
      setBusy(false);
    }
  }

  async function connectSource(documentId: string) {
    if (busy) return;
    setBusy(true);
    setConnectingId(documentId);
    setMessage("");
    const controller = new AbortController();
    connection.current = controller;
    try {
      for (let attempt = 0; attempt < 40; attempt++) {
        const response = await fetch("/api/max/documents/knowledge", {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({ courseId, documentId, ...(aiImportEnabled ? { retry: attempt === 0 } : {}) }),
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) { onRenew(); return; }
        const result: { status?: string } = await response.json().catch(() => ({}));
        if (response.ok && result.status === "APPROVED") {
          setMessage("Источник проверен и подключён к AI.");
        } else if (result.status === "PROCESSING") {
          setMessage("Готовим документ для AI. После обработки подключение сохранится автоматически.");
          if (attempt < 39) {
            await waitForIndex(controller.signal);
            continue;
          }
          setMessage("Документ ещё обрабатывается. Можно уйти с экрана и позже нажать «Подключить к AI» ещё раз. Копия не создастся.");
        } else if (result.status === "SOURCE_BUSY") {
          setMessage("В этом курсе обрабатывается другой документ. Дождитесь завершения и повторите подключение.");
        } else if (result.status === "INDEXING_FAILED") {
          setMessage("Не удалось обработать источник. Повторите подключение позже. Сотрудникам документ доступен для чтения.");
        } else if (result.status === "SOURCE_NOT_READY") {
          setMessage("Источник ещё не готов для поиска. В «Проверить текст» скачайте текст для AI и передайте администратору. После обработки повторите подключение.");
        } else if (result.status === "UNSUPPORTED_FORMAT") {
          setMessage("Для AI пока подходят .txt и .md. PDF можно читать в курсе, но подключить его этой кнопкой нельзя.");
        } else if (response.status === 409) {
          setMessage("Документ или его подключение изменились. Обновите список. Если проблема повторится, попросите администратора проверить источник.");
        } else {
          setMessage("Не удалось подключить источник. Проверьте доступ и повторите позже.");
        }
        setRefreshKey((value) => value + 1);
        break;
      }
    } catch {
      if (controller.signal.aborted) return;
      setMessage("Нет ответа от сервера. Обновите список, чтобы проверить результат подключения.");
    } finally {
      if (connection.current === controller) {
        connection.current = null;
        setBusy(false);
        setConnectingId(null);
      }
    }
  }

  function downloadPreparedText() {
    if (!preview || !/\.(txt|md)$/i.test(preview.sourceName)) return;
    const extension = /\.md$/i.test(preview.sourceName) ? "md" : "txt";
    const url = URL.createObjectURL(new Blob([preview.contentText], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `max-source-${preview.id}.${extension}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <section className={styles.workDocuments} aria-labelledby="max-manager-documents-title">
    <h3 id="max-manager-documents-title">Рабочие документы курса</h3>
    <p>Загрузите документ, проверьте текст и опубликуйте. Для новой редакции укажите, что изменилось, и подготовьте один вопрос. Черновики сотрудникам не видны. Не загружайте конфиденциальные документы на пилотный стенд.</p>
    <p>Публикация открывает документ для чтения. {aiConnectionEnabled
      ? aiImportEnabled
        ? "После проверки и публикации нажмите «Подключить к AI». Проверенный текст передастся в поиск автоматически. Для PDF используется извлечённый текст, оригинал сохраняется отдельно."
        : "В «Проверить текст» скачайте текст для AI и передайте администратору. После обработки нажмите «Подключить к AI»."
      : "AI для этого курса пока не настроен."}</p>
    <form onSubmit={(event) => void upload(event)}>
      <label className={styles.reportSelect}>Это новая редакция?
        <select value={supersedesId} onChange={(event) => {
          const previous = documents.find((document) => document.id === event.target.value);
          setSupersedesId(event.target.value);
          if (previous) setTitle(previous.title);
        }}>
          <option value="">Нет, новый документ</option>
          {documents.filter((document) => document.approvedAt && !document.revokedAt &&
            !documents.some((candidate) => candidate.supersedesId === document.id)).map((document) =>
            <option key={document.id} value={document.id}>{document.title}, версия {document.versionNumber}</option>)}
        </select>
      </label>
      <label className={styles.reportSelect}>Название
        <input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={120} />
      </label>
      {supersedesId && <div className={styles.documentRevisionForm}>
        <label className={styles.reportSelect}>Что изменилось
          <textarea value={changeSummary} onChange={(event) => setChangeSummary(event.target.value)}
            required maxLength={500} rows={3} />
        </label>
        <label className={styles.reportSelect}>Вопрос по новой редакции
          <input value={checkQuestion} onChange={(event) => setCheckQuestion(event.target.value)}
            required maxLength={240} />
        </label>
        {checkOptions.map((option, index) => <label className={styles.reportSelect} key={index}>
          Вариант {index + 1}
          <input value={option} onChange={(event) => setCheckOptions((current) =>
            current.map((value, currentIndex) => currentIndex === index ? event.target.value : value))}
            required minLength={2} maxLength={160} />
        </label>)}
        <label className={styles.reportSelect}>Правильный вариант
          <select value={checkCorrectIndex} onChange={(event) => setCheckCorrectIndex(Number(event.target.value))}>
            <option value={0}>Первый</option>
            <option value={1}>Второй</option>
            <option value={2}>Третий</option>
          </select>
        </label>
      </div>}
      <label className={styles.reportSelect}>Файл .txt или .md до 32 КБ, PDF до 512 КБ и 10 страниц
        <input key={fileInputKey} type="file" accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf" required
          onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </label>
      <button type="submit" className={styles.retry} disabled={busy || !file || !title.trim()}>
        {busy ? "Сохраняем…" : "Загрузить черновик"}
      </button>
    </form>
    {message && <p role="status">{message}</p>}
    {documents.length > 0 && <ul className={styles.documentList}>{documents.map((document) => <li key={document.id}>
      <div><strong>{document.title}</strong><small>{document.sourceName} · версия {document.versionNumber} · {document.revokedAt
        ? "Снята" : document.approvedAt ? "Опубликована" : "Черновик"}</small></div>
      {document.changeSummary && <p>Изменение: {document.changeSummary}</p>}
      <div className={styles.documentActions}>
        <button type="button" className={styles.back} disabled={busy} onClick={() => void showPreview(document.id)}>Проверить текст</button>
        {!document.revokedAt && <button type="button" className={styles.back} disabled={busy}
          onClick={() => setConfirmation({ id: document.id, publish: !document.approvedAt })}>
          {document.approvedAt ? "Снять" : "Опубликовать"}
        </button>}
        {!document.revokedAt && !document.approvedAt && document.supersedesId && <button type="button"
          className={styles.back} disabled={busy}
          onClick={() => setConfirmation({ id: document.id, publish: false })}>Удалить черновик</button>}
        {aiConnectionEnabled && document.approvedAt && !document.revokedAt &&
          (aiImportEnabled || /\.(txt|md)$/i.test(document.sourceName)) && <button type="button"
          className={styles.back} disabled={busy} onClick={() => void connectSource(document.id)}>
          {connectingId === document.id ? "Подключаем источник…" : document.aiConnected ? "Проверить подключение" : "Подключить к AI"}
        </button>}
      </div>
      {aiConnectionEnabled && document.approvedAt && !document.revokedAt && <p>
        {!aiImportEnabled && !/\.(txt|md)$/i.test(document.sourceName) ? "Для AI пока подходят только .txt и .md. PDF доступен для чтения."
          : document.aiConnected ? "Подключение к AI сохранено." : "К AI не подключён."}
      </p>}
      {document.trainingRecipients.length > 0 && <details className={styles.documentTrainingReport}>
        <summary>Повторное обучение: {document.trainingRecipients.filter((recipient) => recipient.passedAt).length}
          {" "}из {document.trainingRecipients.length} прошли</summary>
        <ul>{document.trainingRecipients.map((recipient) => <li key={recipient.userId}>
          {recipient.user.name}: {recipient.passedAt ? "прошёл" : recipient.viewedAt
            ? recipient.attempts >= 3 ? "не прошёл" : "изучает" : "не открыл"}
        </li>)}</ul>
      </details>}
      {confirmation?.id === document.id && <div className={styles.documentConfirm}>
        <p>{confirmation.publish && document.supersedesId
          ? `Старая версия будет снята. Повторное обучение получат ${audience?.length ?? 0} сотрудников с действующим назначением курса. Проверьте список и вопрос перед публикацией.`
          : confirmation.publish ? "После публикации текст увидят сотрудники, которым назначен этот курс. Подтвердить?"
            : document.supersedesId && !document.approvedAt
              ? "Черновик новой редакции будет удалён. Это нельзя отменить. Подтвердить?"
              : "Сотрудники больше не смогут открыть документ. Подтвердить?"}</p>
        {confirmation.publish && document.supersedesId && <>
          <p>Получатели: {audience?.map(({ name }) => name).join(", ") || "список пуст или слишком велик"}</p>
          <p>Для новых назначений после публикации эта проверка не выдаётся автоматически.</p>
        </>}
        <button type="button" className={styles.retry} disabled={busy || Boolean(confirmation.publish &&
          document.supersedesId && (!audience?.length || audience.length > 200))}
          onClick={() => void changePublication(document.id, confirmation.publish)}>Подтвердить</button>
        <button type="button" className={styles.back} onClick={() => setConfirmation(null)}>Отмена</button>
      </div>}
    </li>)}</ul>}
    {preview && <div className={styles.documentPreview}>
      <h4>{preview.title}</h4>
      {preview.changeSummary && <p>Изменение: {preview.changeSummary}</p>}
      <MaxDocumentText text={preview.contentText} />
      {preview.hasOriginal && <MaxDocumentDownload key={preview.id} courseId={courseId} manager
        documentId={preview.id} sourceName={preview.sourceName} token={token} onRenew={onRenew} />}
      {aiConnectionEnabled && !aiImportEnabled && /\.(txt|md)$/i.test(preview.sourceName) && <>
        <p>Этот файл содержит проверяемый текст без изменений кодировки и переносов строк. Передайте администратору именно его.</p>
        <button type="button" className={styles.back} onClick={downloadPreparedText}>Скачать текст для AI</button>
      </>}
      <details>
        <summary>Исходный текст файла</summary>
        <pre>{preview.contentText}</pre>
      </details>
      {preview.checkQuestion && <div className={styles.documentCheckPreview}>
        <strong>{preview.checkQuestion}</strong>
        <ol>{(preview.checkOptionsJson ? JSON.parse(preview.checkOptionsJson) as string[] : []).map((option, index) =>
          <li key={index}>{option}{index === preview.checkCorrectIndex ? " (правильный)" : ""}</li>)}</ol>
      </div>}
      <button type="button" className={styles.back} onClick={() => setPreview(null)}>Закрыть текст</button>
    </div>}
  </section>;
}
