const API_ORIGIN = "https://platform-api2.max.ru";
const RESPONSE_LIMIT = 64 * 1024;

export class MaxBotApiError extends Error {
  constructor(readonly code: "configuration" | "transport" | "http" | "response", readonly status?: number) {
    // Never include upstream bodies, request headers or fetch error causes: they may contain secrets.
    super(`MAX API: ${code}${status === undefined ? "" : ` (${status})`}`);
    this.name = "MaxBotApiError";
  }
}

export type MaxBotProfile = { userId: number; username: string; firstName: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function readResponse(response: Response): Promise<unknown> {
  if (!response.body) throw new MaxBotApiError("response");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > RESPONSE_LIMIT) throw new MaxBotApiError("response");
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function createMaxBotClient(token: string, fetcher: typeof fetch = fetch) {
  if (!token || token.trim() !== token || /[\r\n]/.test(token)) {
    throw new MaxBotApiError("configuration");
  }

  async function request(path: string, body?: unknown): Promise<unknown> {
    try {
      const response = await fetcher(`${API_ORIGIN}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: { Authorization: token, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(8000),
        redirect: "error",
        cache: "no-store",
      });
      if (!response.ok) {
        await response.body?.cancel().catch(() => undefined);
        throw new MaxBotApiError("http", response.status);
      }
      return await readResponse(response);
    } catch (error) {
      if (error instanceof MaxBotApiError) throw error;
      if (error instanceof SyntaxError) throw new MaxBotApiError("response");
      throw new MaxBotApiError("transport");
    }
  }

  async function sendWithApp(userId: number, botUsername: string, text: string): Promise<string> {
    if (!Number.isSafeInteger(userId) || userId <= 0 || !/^[a-zA-Z0-9_]+$/.test(botUsername)) {
      throw new MaxBotApiError("configuration");
    }
    const data = await request(`/messages?user_id=${userId}`, {
      text,
      attachments: [{
        type: "inline_keyboard",
        payload: { buttons: [[{ type: "open_app", text: "Открыть обучение", web_app: botUsername }]] },
      }],
    });
    if (!isObject(data) || !isObject(data.message) || !isObject(data.message.body)
      || typeof data.message.body.mid !== "string" || !data.message.body.mid) {
      throw new MaxBotApiError("response");
    }
    return data.message.body.mid;
  }

  return {
    async getProfile(): Promise<MaxBotProfile> {
      const data = await request("/me");
      if (!isObject(data) || data.is_bot !== true || !Number.isSafeInteger(data.user_id)
        || (data.user_id as number) <= 0 || typeof data.username !== "string"
        || !/^[a-zA-Z0-9_]+$/.test(data.username) || typeof data.first_name !== "string") {
        throw new MaxBotApiError("response");
      }
      return { userId: data.user_id as number, username: data.username, firstName: data.first_name };
    },

    // Call only after authenticated event intake and durable deduplication are in place.
    // Do not automatically retry: a timed-out POST may already have delivered the message.
    async sendWelcome(userId: number, botUsername: string): Promise<string> {
      return sendWithApp(userId, botUsername,
        "Добро пожаловать в Prodigy! Откройте приложение, чтобы связать учётную запись сотрудника и увидеть назначенные курсы. Код привязки вводите только внутри приложения, не отправляйте его в чат.");
    },

    async sendRevision(userId: number, botUsername: string): Promise<string> {
      return sendWithApp(userId, botUsername,
        "В назначенном курсе обновился рабочий документ. Откройте обучение, прочитайте новую редакцию и пройдите короткую проверку.");
    },

    async sendHelp(userId: number, botUsername: string): Promise<string> {
      return sendWithApp(userId, botUsername,
        "Курсы, материалы, вопросы AI и тесты сейчас доступны в приложении по кнопке ниже. Выберите назначенный курс. Если профиль ещё не связан, получите код у HR и введите его внутри приложения. Не отправляйте в чат коды, личные данные или рабочие документы. Вопросы и тесты прямо в чате добавим отдельно.");
    },
  };
}
