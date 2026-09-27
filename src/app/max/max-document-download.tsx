"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./max.module.css";

type Props = {
  courseId: string;
  documentId: string;
  sourceName: string;
  token: string;
  onRenew: () => void;
  manager?: boolean;
};

export function MaxDocumentDownload({
  courseId,
  documentId,
  sourceName,
  token,
  onRenew,
  manager = false,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function download() {
    if (busy) return;
    const controller = new AbortController();
    request.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    setBusy(true);
    setMessage("");
    try {
      const query = new URLSearchParams({ courseId, documentId, download: "original" });
      if (manager) query.set("scope", "manager");
      const response = await fetch(`/api/max/documents?${query}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
        cache: "no-store",
      });
      if (response.status === 401) {
        onRenew();
        return;
      }
      if (!response.ok) throw new Error("Document unavailable");
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = sourceName.replace(/[\\/\u0000-\u001f]/g, "_");
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setMessage("Файл передан браузеру. Если он не открылся, проверьте загрузки.");
    } catch {
      setMessage(controller.signal.aborted
        ? "Сервер не ответил вовремя. Попробуйте скачать ещё раз."
        : "Не удалось скачать файл. Проверьте доступ и повторите.");
    } finally {
      window.clearTimeout(timeout);
      if (request.current === controller) setBusy(false);
    }
  }

  const extension = sourceName.split(".").at(-1)?.toLowerCase() ?? "txt";
  return (
    <div>
      <button
        type="button"
        className={styles.back}
        disabled={busy}
        onClick={() => void download()}
      >
        {busy ? "Получаем файл..." : `Скачать оригинал (.${extension})`}
      </button>
      {message && <p role="status">{message}</p>}
    </div>
  );
}
