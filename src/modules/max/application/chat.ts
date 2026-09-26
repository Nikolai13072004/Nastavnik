import type { MaxCourse, MaxLearnerIdentity } from "./list-courses";
import type { MaxQuizQuestion } from "./quiz-delivery";
import type { KnowledgeAnswer } from "./verify-knowledge-answer";

export type ChatInput =
  | { type: "text"; text: string; messageId: string }
  | {
      type: "callback";
      payload: string;
      callbackId: string;
      messageId: string;
    };
export type ChatEvent = {
  kind: "CHAT";
  userId: number;
  timestamp: number;
  input: ChatInput;
};
export type ChatButton =
  | { type: "callback"; text: string; payload: string }
  | { type: "open_app"; text: string; web_app: string; payload?: string };
export type ChatReply = {
  text?: string;
  buttons?: ChatButton[][];
  notification?: string;
};
export type ChatCourse = {
  id: string;
  title: string;
  completed: boolean;
  materials: Array<{ completed: boolean }>;
  quizzes: Array<{
    id: string;
    title: string;
    questionCount: number;
    chatSupported: boolean;
    maxAttempts: number;
    attemptsUsed: number;
    status: string;
    bestCorrectAnswers: number;
  }>;
};
export type ChatQuiz = {
  id: string;
  courseId?: string;
  attemptId: string;
  questions: MaxQuizQuestion[];
  answers: Record<string, number>;
};
export type ChatState = {
  version: string;
  courseId?: string;
  courseIds?: string[];
  courseSearch?: string;
  quizIds?: string[];
  quizPage?: number;
  quizPaused?: boolean;
  documentIds?: string[];
  aiRequestedAt?: number[];
  quiz?: ChatQuiz;
};
export interface ChatSessionRepository {
  load(identity: MaxLearnerIdentity): Promise<ChatState | null>;
  save(identity: MaxLearnerIdentity, state: ChatState): Promise<void>;
  clear(maxUserId: string): Promise<void>;
}
export interface ChatLearning {
  course(identity: MaxLearnerIdentity, courseId: string): Promise<ChatCourse>;
  ask(
    identity: MaxLearnerIdentity,
    courseId: string,
    question: string,
  ): Promise<KnowledgeAnswer>;
  document(
    identity: MaxLearnerIdentity,
    courseId: string,
    documentId: string,
  ): Promise<{ title: string; contentText: string }>;
  start(
    identity: MaxLearnerIdentity,
    courseId: string,
    quizId: string,
  ): Promise<{ attemptId: string; questions: MaxQuizQuestion[] }>;
  submit(
    identity: MaxLearnerIdentity,
    courseId: string,
    quiz: ChatQuiz,
  ): Promise<{
    outcome: string;
    correctAnswers: number;
    totalQuestions: number;
  }>;
}
export interface ChatAccess {
  identity(maxUserId: string): Promise<MaxLearnerIdentity | null>;
  courses(identity: MaxLearnerIdentity): Promise<MaxCourse[] | null>;
}
export class ChatLearningError extends Error {
  constructor(readonly code: string) {
    super("MAX learning unavailable");
  }
}

export function parseChatEvent(
  value: unknown,
  now: number,
): ChatEvent | "ignored" {
  if (!value || typeof value !== "object") return "ignored";
  const event = value as Record<string, unknown>;
  if (
    !Number.isSafeInteger(event.timestamp) ||
    (event.timestamp as number) < now - 300_000 ||
    (event.timestamp as number) > now + 60_000
  )
    return "ignored";
  const message = event.message as Record<string, unknown> | undefined;
  const recipient = message?.recipient as Record<string, unknown> | undefined;
  const body = message?.body as Record<string, unknown> | undefined;
  if (
    recipient?.chat_type !== "dialog" ||
    typeof body?.mid !== "string" ||
    !body.mid.trim() ||
    body.mid.length > 256
  )
    return "ignored";
  let user: Record<string, unknown> | undefined;
  let input: ChatInput;
  if (event.update_type === "message_created") {
    user = message?.sender as Record<string, unknown> | undefined;
    if (typeof body.text !== "string" || !body.text.trim()) return "ignored";
    // Oversized messages become help, never an unbounded AI request or stored attachment.
    const text = body.text.trim();
    input = {
      type: "text",
      messageId: body.mid,
      text:
        text.length <= 500 && (text.length < 3 || isSafeChatQuestion(text))
          ? text
          : "помощь",
    };
  } else if (event.update_type === "message_callback") {
    const callback = event.callback as Record<string, unknown> | undefined;
    user = callback?.user as Record<string, unknown> | undefined;
    if (
      typeof callback?.callback_id !== "string" ||
      !callback.callback_id.trim() ||
      callback.callback_id.length > 256 ||
      typeof callback.payload !== "string" ||
      !/^chat:[a-zA-Z0-9_-]{16}:[a-z]+(?::\d{1,3})?$/.test(callback.payload) ||
      recipient.user_id !== user?.user_id
    )
      return "ignored";
    input = {
      type: "callback",
      messageId: body.mid,
      callbackId: callback.callback_id,
      payload: callback.payload,
    };
  } else {
    return "ignored";
  }
  if (
    user?.is_bot !== false ||
    !Number.isSafeInteger(user.user_id) ||
    (user.user_id as number) <= 0
  )
    return "ignored";
  return {
    kind: "CHAT",
    userId: user.user_id as number,
    timestamp: event.timestamp as number,
    input,
  };
}

export function isSafeChatQuestion(text: string) {
  return (
    text.length >= 3 &&
    text.length <= 500 &&
    !/\b\d{4,8}\b|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:\+?\d[\s()-]*){10,}|(?:bearer|password|пароль|токен)\s*[:=]|[a-zA-Z0-9_-]{32,}/iu.test(
      text,
    )
  );
}
