import type { ChatLearning } from "../application/chat";
import { ChatLearningError } from "../application/chat";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { createMaxSessionCodec } from "./learner-session";

export function createChatLearningClient(botToken: string, origin: string, fetcher: typeof fetch = fetch): ChatLearning {
  const base = new URL(origin);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password ||
      base.pathname !== "/" || base.search || base.hash) throw new Error("Invalid internal learning origin");
  const sessions = createMaxSessionCodec(botToken);

  async function request(identity: MaxLearnerIdentity, path: string, body?: unknown) {
    try {
      const response = await fetcher(new URL(path, base), {
        method: body === undefined ? "GET" : "POST",
        headers: { Authorization: `Bearer ${sessions.issue(identity).token}`, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(body && path === "/api/max/knowledge" ? 60_000 : 15_000),
        redirect: "error", cache: "no-store",
      });
      if (!response.body) throw new ChatLearningError("UNAVAILABLE");
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 512 * 1024) throw new ChatLearningError("UNAVAILABLE");
          chunks.push(value);
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
      const data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!response.ok) {
        const code = typeof data.error === "string" && /^[A-Z_]{1,64}$/.test(data.error) ? data.error : "UNAVAILABLE";
        throw new ChatLearningError(code);
      }
      return data;
    } catch (error) {
      if (error instanceof ChatLearningError) throw error;
      throw new ChatLearningError("UNAVAILABLE");
    }
  }
  return {
    async course(identity, courseId) {
      return (await request(identity, `/api/max/course?courseId=${encodeURIComponent(courseId)}`)).course;
    },
    async ask(identity, courseId, question) {
      return request(identity, "/api/max/knowledge", { courseId, question });
    },
    async document(identity, courseId, documentId) {
      return (await request(identity, `/api/max/documents?courseId=${encodeURIComponent(courseId)}&documentId=${encodeURIComponent(documentId)}`)).document;
    },
    async start(identity, courseId, quizId) {
      return request(identity, "/api/max/quiz", { action: "start", courseId, quizId });
    },
    async submit(identity, courseId, quiz) {
      return (await request(identity, "/api/max/quiz", {
        action: "submit", courseId, quizId: quiz.id, attemptId: quiz.attemptId, answers: quiz.answers,
      })).result;
    },
  };
}
