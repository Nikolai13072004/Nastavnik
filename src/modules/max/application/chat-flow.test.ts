import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatFlow } from "./chat-flow";
import { ChatLearningError, isSafeChatQuestion, parseChatEvent } from "./chat";
import type { ChatCourse, ChatInput, ChatLearning, ChatReply, ChatState } from "./chat";
import { isShortChatQuiz } from "./quiz-delivery";

const identity = { maxUserId: "123", userId: "learner", organizationId: "org", linkedAt: new Date(0).toISOString() };
const course: ChatCourse = { id: "course", title: "Первый день", completed: false,
  materials: [{ completed: true }], quizzes: [{ id: "quiz", title: "Проверка", questionCount: 2,
    chatSupported: true, maxAttempts: 3, attemptsUsed: 0, status: "NOT_STARTED", bestCorrectAnswers: 0 }] };
const questions = [
  { id: "q1", prompt: "Первый вопрос", options: ["Да", "Нет"] },
  { id: "q2", prompt: "Второй вопрос", options: ["Да", "Нет"] },
];
const text = (value: string): ChatInput => ({ type: "text", text: value, messageId: "incoming" });
function callback(reply: ChatReply, label: string): ChatInput {
  const button = reply.buttons?.flat().find((item) => item.text === label);
  assert.ok(button?.type === "callback", `${label}: expected callback`);
  return { type: "callback", payload: button.payload, callbackId: "cb", messageId: "bot-message" };
}
function fixture() {
  let state: ChatState | null = null;
  let linked = true;
  let assigned = true;
  let starts = 0;
  let submits = 0;
  let asks = 0;
  let sequence = 0;
  let receivedAnswers: Record<string, number> = {};
  const learning: ChatLearning = {
    course: async () => structuredClone(course),
    ask: async () => {
      asks++;
      return { answer: "Подтверждённый ответ", refused: false, sources: [{ documentId: "vedomo-document",
        documentHash: "hash", title: "Правила", section: "Доступ", snippet: "Подтверждение",
        pageStart: null, pageEnd: null, courseDocumentId: "exact-document" }] };
    },
    document: async (_, courseId, documentId) => {
      assert.equal(courseId, "course");
      assert.equal(documentId, "exact-document");
      return { title: "Правила", contentText: "Точный документ" };
    },
    start: async () => { starts++; return { attemptId: "attempt", questions }; },
    submit: async (_, __, quiz) => {
      submits++;
      receivedAnswers = structuredClone(quiz.answers);
      return { outcome: "PASSED", correctAnswers: 2, totalQuestions: 2 };
    },
  };
  const flow = createChatFlow({
    botUsername: "test_bot", version: () => String(++sequence).padStart(16, "0"), learning,
    access: { identity: async () => linked ? identity : null,
      courses: async () => assigned ? [{ id: "course", title: "Первый день", expiresAt: null }] : [] },
    sessions: { load: async () => structuredClone(state), save: async (_, value) => { state = structuredClone(value); },
      clear: async () => { state = null; } },
  });
  return { flow: (input: ChatInput) => flow("123", input), learning,
    unlink: () => { linked = false; }, revoke: () => { assigned = false; },
    counts: () => ({ starts, submits, asks }), state: () => state, answers: () => receivedAnswers };
}
async function select(f: ReturnType<typeof fixture>) {
  const menu = await f.flow(text("курсы"));
  return f.flow(callback(menu, "Первый день"));
}
async function begin(f: ReturnType<typeof fixture>) {
  const panel = await select(f);
  const tests = await f.flow(callback(panel, "Пройти тест"));
  const confirmation = await f.flow(callback(tests, "Проверка"));
  assert.equal(f.counts().starts, 0);
  return f.flow(callback(confirmation, "Начать или продолжить"));
}

test("chat menus select an assigned course without starting or submitting a test", async () => {
  const f = fixture();
  const panel = await select(f);
  assert.match(panel.text!, /Первый день/);
  assert.equal(panel.buttons?.flat().length, 5);
  const progress = await f.flow(callback(panel, "Прогресс"));
  assert.match(progress.text!, /Материалы: 1 из 1/);
  assert.deepEqual(f.counts(), { starts: 0, submits: 0, asks: 0 });
});

test("quiz has one question per turn, explicit start and a shared result; old buttons do not answer again", async () => {
  const f = fixture();
  const first = await begin(f);
  assert.match(first.text!, /Вопрос 1 из 2/);
  const firstAnswer = callback(first, "1");
  const second = await f.flow(firstAnswer);
  assert.match(second.text!, /Вопрос 2 из 2/);
  assert.ok((await f.flow(firstAnswer)).notification);
  const result = await f.flow(callback(second, "2"));
  assert.match(result.text!, /Тест пройден/);
  assert.deepEqual(f.answers(), { q1: 0, q2: 1 });
  assert.deepEqual(f.counts(), { starts: 1, submits: 1, asks: 0 });
  assert.ok((await f.flow(callback(second, "2"))).notification);
});

test("pause keeps answers; free text during a quiz does not call AI", async () => {
  const f = fixture();
  const first = await begin(f);
  const second = await f.flow(callback(first, "1"));
  const paused = await f.flow(callback(second, "Продолжить позже"));
  const resumed = await f.flow(callback(paused, "Продолжить тест"));
  assert.match(resumed.text!, /Вопрос 2 из 2/);
  assert.match((await f.flow(text("Как получить доступ?"))).text!, /Вопрос 2 из 2/);
  assert.equal(f.counts().asks, 0);
});

test("uncertain assessment submission preserves the exact answers for idempotent recovery", async () => {
  const f = fixture();
  const first = await begin(f);
  const second = await f.flow(callback(first, "1"));
  const submit = f.learning.submit;
  f.learning.submit = async () => { throw new ChatLearningError("UNAVAILABLE"); };
  assert.match((await f.flow(callback(second, "1"))).text!, /недоступно/);
  assert.deepEqual(f.state()?.quiz?.answers, { q1: 0, q2: 0 });
  f.learning.submit = submit;
  assert.match((await f.flow(text("тест"))).text!, /Тест пройден/);
  assert.equal(f.counts().starts, 1);
});

test("material prerequisite is explained and no chat attempt is fabricated", async () => {
  const f = fixture();
  f.learning.start = async () => { throw new ChatLearningError("MATERIAL_REQUIRED"); };
  assert.match((await begin(f)).text!, /Попытка теста не началась/);
  assert.equal(f.state()?.quiz, undefined);
});

test("AI uses the selected course and exact source, never a model-provided link", async () => {
  const f = fixture();
  await select(f);
  const answer = await f.flow(text("Как устроено обучение?"));
  assert.match(answer.text!, /Подтверждённый ответ/);
  const source = await f.flow(callback(answer, "Открыть источник 1"));
  assert.match(source.text!, /Точный документ/);
  assert.equal(f.counts().asks, 1);
});

test("revoked course and unlinked profile cannot reuse quiz or AI buttons", async () => {
  const f = fixture();
  const first = await begin(f);
  f.revoke();
  assert.match((await f.flow(callback(first, "1"))).text!, /курсов пока нет/);
  assert.equal(f.counts().submits, 0);
  f.unlink();
  const reply = await f.flow(text("Вопрос по документу"));
  assert.match(reply.text!, /Сначала свяжите профиль/);
  assert.equal(f.state(), null);
  assert.equal(f.counts().asks, 0);
});

test("access is rechecked after AI before returning private text or sources", async () => {
  const f = fixture();
  await select(f);
  const ask = f.learning.ask;
  f.learning.ask = async (...args) => { f.revoke(); return ask(...args); };
  const answer = await f.flow(text("Как получить доступ?"));
  assert.doesNotMatch(answer.text!, /Подтверждённый/);
  assert.equal(f.state()?.documentIds, undefined);
});

test("sensitive questions are not sent to AI, and oversized quiz messages stay in Mini App", () => {
  for (const value of ["123456", "мой код 123456", "me@example.com", "+7 999 123-45-67", "пароль=secret"]) {
    assert.equal(isSafeChatQuestion(value), false);
  }
  assert.equal(isSafeChatQuestion("Как устроено обучение?"), true);
  assert.equal(isShortChatQuiz(questions), true);
  assert.equal(isShortChatQuiz([{ ...questions[0], prompt: "x".repeat(3000) }]), false);
  assert.equal(isShortChatQuiz(Array.from({ length: 11 }, () => questions[0])), false);
});

test("intake accepts only fresh private messages and callbacks belonging to the same user", () => {
  const now = Date.now();
  const event = { update_type: "message_created", timestamp: now,
    message: { sender: { user_id: 123, is_bot: false, name: "private" },
      recipient: { chat_type: "dialog", user_id: 99 }, body: { mid: "mid", text: "мой код 123456" } } };
  const parsed = parseChatEvent(event, now);
  assert.notEqual(parsed, "ignored");
  assert.ok(parsed !== "ignored" && parsed.input.type === "text");
  assert.equal(parsed.input.text, "помощь");
  assert.ok(!JSON.stringify(parsed).includes("private"));
  assert.equal(parseChatEvent({ ...event, timestamp: now - 300_001 }, now), "ignored");
  const cb = { ...event, update_type: "message_callback", callback: { callback_id: "cb", user: { user_id: 123, is_bot: false },
    payload: "chat:0000000000000001:progress" } };
  assert.equal(parseChatEvent(cb, now), "ignored");
  cb.message.recipient.user_id = 123;
  assert.notEqual(parseChatEvent(cb, now), "ignored");
  cb.message.recipient.chat_type = "chat";
  assert.equal(parseChatEvent(cb, now), "ignored");
});
