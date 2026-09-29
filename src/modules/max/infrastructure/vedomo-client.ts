import type { KnowledgeAnswer, KnowledgeSource } from "../application/verify-knowledge-answer";

const RESPONSE_LIMIT = 64 * 1024;

export class VedomoClientError extends Error {
  constructor(readonly code: "configuration" | "transport" | "http" | "response", readonly status?: number) {
    super(`Knowledge API: ${code}${status === undefined ? "" : ` (${status})`}`);
    this.name = "VedomoClientError";
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseSource(value: unknown): KnowledgeSource | null {
  if (!isObject(value) || typeof value.document_id !== "string" || !value.document_id ||
      typeof value.document_hash !== "string" || !value.document_hash ||
      typeof value.title !== "string" || !value.title ||
      typeof value.section !== "string" || typeof value.snippet !== "string" || !value.snippet.trim() ||
      (value.page_start !== null && (!Number.isInteger(value.page_start) || (value.page_start as number) < 0)) ||
      (value.page_end !== null && (!Number.isInteger(value.page_end) || (value.page_end as number) < 0))) {
    return null;
  }
  return {
    documentId: value.document_id,
    documentHash: value.document_hash,
    title: value.title,
    section: value.section,
    pageStart: value.page_start as number | null,
    pageEnd: value.page_end as number | null,
    snippet: value.snippet,
  };
}

async function readResponse(response: Response): Promise<unknown> {
  if (!response.body) throw new VedomoClientError("response");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > RESPONSE_LIMIT) throw new VedomoClientError("response");
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function createVedomoClient(origin: string, token: string, fetcher: typeof fetch = fetch) {
  let endpoint: string;
  try {
    const url = new URL(origin);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash ||
        !/^[A-Za-z0-9_-]{43,128}$/.test(token)) throw new Error("Invalid configuration");
    endpoint = `${url.origin}/api/integrations/prodigy/chat`;
  } catch {
    throw new VedomoClientError("configuration");
  }

  return {
    async importDocument(organizationId: string, courseId: string, document: {
      sourceName: string; contentText: string; contentHash: string; retry: boolean;
    }) {
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(organizationId) ||
          !/^[A-Za-z0-9_-]{1,128}$/.test(courseId) || !/^[a-f0-9]{64}$/.test(document.contentHash) ||
          !document.contentText.trim() || Buffer.byteLength(document.contentText) > 32 * 1024 ||
          !/^[^/\\\x00-\x1f]{1,180}\.(txt|md)$/i.test(document.sourceName)) {
        throw new VedomoClientError("configuration");
      }
      const body = JSON.stringify({ source_name: document.sourceName, content_text: document.contentText,
        content_hash: document.contentHash, retry: document.retry });
      if (Buffer.byteLength(body) > 64 * 1024) throw new VedomoClientError("configuration");
      try {
        const response = await fetcher(`${new URL(endpoint).origin}/api/integrations/prodigy/documents/import`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json",
            "X-Prodigy-Organization-ID": organizationId, "X-Prodigy-Course-ID": courseId },
          body,
          signal: AbortSignal.timeout(10_000),
          redirect: "error",
          cache: "no-store",
        });
        if (!response.ok) {
          await response.body?.cancel().catch(() => undefined);
          throw new VedomoClientError("http", response.status);
        }
        const data = await readResponse(response);
        if (!isObject(data) || !["READY", "PROCESSING", "BUSY", "ERROR"].includes(String(data.status)) ||
            data.document_hash !== document.contentHash ||
            (data.status === "READY" && (typeof data.document_id !== "string" ||
              !/^[A-Za-z0-9_-]{1,128}$/.test(data.document_id)))) throw new VedomoClientError("response");
        return { status: data.status as "READY" | "PROCESSING" | "BUSY" | "ERROR",
          documentId: typeof data.document_id === "string" ? data.document_id : undefined,
          documentHash: document.contentHash };
      } catch (error) {
        if (error instanceof VedomoClientError) throw error;
        throw new VedomoClientError(error instanceof SyntaxError ? "response" : "transport");
      }
    },
    async findDocument(organizationId: string, courseId: string, documentHash: string) {
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(organizationId) ||
          !/^[A-Za-z0-9_-]{1,128}$/.test(courseId) || !/^[a-f0-9]{64}$/.test(documentHash)) {
        throw new VedomoClientError("configuration");
      }
      try {
        const response = await fetcher(
          `${new URL(endpoint).origin}/api/integrations/prodigy/documents/by-hash/${documentHash}`,
          {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
              "X-Prodigy-Organization-ID": organizationId,
              "X-Prodigy-Course-ID": courseId,
            },
            signal: AbortSignal.timeout(10_000),
            redirect: "error",
            cache: "no-store",
          },
        );
        if (!response.ok) {
          await response.body?.cancel().catch(() => undefined);
          if (response.status === 404) return null;
          throw new VedomoClientError("http", response.status);
        }
        const data = await readResponse(response);
        if (!isObject(data) || typeof data.document_id !== "string" ||
            !/^[A-Za-z0-9_-]{1,128}$/.test(data.document_id) || data.document_hash !== documentHash ||
            typeof data.title !== "string" || !data.title.trim()) {
          throw new VedomoClientError("response");
        }
        return { documentId: data.document_id, documentHash, title: data.title };
      } catch (error) {
        if (error instanceof VedomoClientError) throw error;
        if (error instanceof SyntaxError) throw new VedomoClientError("response");
        throw new VedomoClientError("transport");
      }
    },
    async getDocument(organizationId: string, courseId: string, documentId: string) {
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(organizationId) ||
          !/^[A-Za-z0-9_-]{1,128}$/.test(courseId) ||
          !/^[A-Za-z0-9_-]{1,128}$/.test(documentId)) {
        throw new VedomoClientError("configuration");
      }
      let response: Response;
      try {
        response = await fetcher(`${new URL(endpoint).origin}/api/integrations/prodigy/documents/${documentId}`, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            "X-Prodigy-Organization-ID": organizationId,
            "X-Prodigy-Course-ID": courseId,
          },
          signal: AbortSignal.timeout(10_000),
          redirect: "error",
          cache: "no-store",
        });
        if (!response.ok) {
          await response.body?.cancel().catch(() => undefined);
          throw new VedomoClientError("http", response.status);
        }
        const data = await readResponse(response);
        if (!isObject(data) || data.document_id !== documentId ||
            typeof data.document_hash !== "string" || !/^[a-f0-9]{64}$/.test(data.document_hash) ||
            typeof data.title !== "string" || !data.title.trim()) {
          throw new VedomoClientError("response");
        }
        return { documentId: data.document_id, documentHash: data.document_hash, title: data.title };
      } catch (error) {
        if (error instanceof VedomoClientError) throw error;
        if (error instanceof SyntaxError) throw new VedomoClientError("response");
        throw new VedomoClientError("transport");
      }
    },
    async ask(organizationId: string, courseId: string, question: string, documentIds?: string[]): Promise<KnowledgeAnswer> {
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(organizationId) || !/^[A-Za-z0-9_-]{1,128}$/.test(courseId) ||
          question.trim().length < 3 || question.length > 500 || (documentIds !== undefined &&
            (!documentIds.length || documentIds.length > 40 ||
              documentIds.some((id) => !/^[A-Za-z0-9_-]{1,128}$/.test(id))))) {
        throw new VedomoClientError("configuration");
      }
      let response: Response;
      try {
        response = await fetcher(endpoint, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "X-Prodigy-Organization-ID": organizationId,
            "X-Prodigy-Course-ID": courseId,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ question: question.trim(), ...(documentIds ? { document_ids: documentIds } : {}) }),
          signal: AbortSignal.timeout(110_000),
          redirect: "error",
          cache: "no-store",
        });
        if (!response.ok) {
          await response.body?.cancel().catch(() => undefined);
          throw new VedomoClientError("http", response.status);
        }
        const data = await readResponse(response);
        if (!isObject(data) || typeof data.answer !== "string" || typeof data.refused !== "boolean" ||
            !Array.isArray(data.sources) || data.sources.length > 10) throw new VedomoClientError("response");
        const sources = data.sources.map(parseSource);
        if (sources.some((source) => source === null) || (data.refused && sources.length !== 0) ||
            (!data.refused && (!data.answer.trim() || sources.length === 0))) {
          throw new VedomoClientError("response");
        }
        return { answer: data.answer, refused: data.refused, sources: sources as KnowledgeSource[] };
      } catch (error) {
        if (error instanceof VedomoClientError) throw error;
        if (error instanceof SyntaxError) throw new VedomoClientError("response");
        throw new VedomoClientError("transport");
      }
    },
  };
}
