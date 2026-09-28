import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatFlow } from "./chat-flow";
import { ChatLearningError, isSafeChatQuestion, parseChatEvent } from "./chat";
import type {
  ChatCourse,
  ChatInput,
  ChatLearning,
  ChatReply,
  ChatState,
} from "./chat";
import { isShortChatQuiz } from "./quiz-delivery";
import type { MaxCourse } from "./list-courses";

const identity = {
  maxUserId: "123",
  userId: "learner",
  organizationId: "org",
  linkedAt: new Date(0).toISOString(),
};
const course: ChatCourse = {
  id: "course",
  title: "Первый день",
  completed: false,
  materials: [{ completed: true }],
  quizzes: [
    {
      id: "quiz",
      title: "Проверка",
      questionCount: 2,
      chatSupported: true,
      maxAttempts: 3,
      attemptsUsed: 0,
      status: "NOT_STARTED",
      bestCorrectAnswers: 0,
    },
  ],
};
const questions = [
  { id: "q1", prompt: "Первый вопрос", options: ["Да", "Нет"] },
  { id: "q2", prompt: "Второй вопрос", options: ["Да", "Нет"] },
];
const text = (value: string): ChatInput => ({
  type: "text",
  text: value,
  messageId: "incoming",
});
function callback(reply: ChatReply, label: string): ChatInput {
  const button = reply.buttons?.flat().find((item) => item.text === label);
  assert.ok(button?.type === "callback", `${label}: expected callback`);
  return {
    type: "callback",
    payload: button.payload,
    callbackId: "cb",
    messageId: "bot-message",
  };
}
function fixture(
  availableCourses: MaxCourse[] = [
    { id: "course", title: "Первый день", expiresAt: null },
  ],
) {
  let state: ChatState | null = null;
  let linked = true;
  let assigned = true;
  let starts = 0;
  let submits = 0;
  let asks = 0;
  let courseReads = 0;
  let sequence = 0;
  let receivedAnswers: Record<string, number> = {};
  const learning: ChatLearning = {
    course: async () => {
      courseReads++;
      return structuredClone(course);
    },
    ask: async () => {
      asks++;
      return {
        answer: "Подтверждённый ответ",
        refused: false,
        sources: [
          {
            documentId: "vedomo-document",
            documentHash: "hash",
            title: "Правила",
            section: "Доступ",
            snippet: "Подтверждение",
            pageStart: null,
            pageEnd: null,
            courseDocumentId: "exact-document",
          },
        ],
      };
    },
    document: async (_, courseId, documentId) => {
      assert.equal(courseId, "course");
      assert.equal(documentId, "exact-document");
      return { title: "Правила", contentText: "Точный документ" };
    },
    start: async () => {
      starts++;
      return { attemptId: "attempt", questions };
    },
    submit: async (_, __, quiz) => {
      submits++;
      receivedAnswers = structuredClone(quiz.answers);
      return { outcome: "PASSED", correctAnswers: 2, totalQuestions: 2 };
    },
  };
  const flow = createChatFlow({
    botUsername: "test_bot",
    version: () => String(++sequence).padStart(16, "0"),
    learning,
    access: {
      identity: async () => (linked ? identity : null),
      courses: async () => (assigned ? availableCourses : []),
    },
    sessions: {
      load: async () => structuredClone(state),
      save: async (_, value) => {
        state = structuredClone(value);
      },
      clear: async () => {
        state = null;
      },
    },
  });
  return {
    flow: (input: ChatInput) => flow("123", input),
    learning,
    unlink: () => {
      linked = false;
    },
    revoke: () => {
      assigned = false;
    },
    expireSession: () => {
      state = null;
    },
    counts: () => ({ starts, submits, asks }),
    courseReads: () => courseReads,
    state: () => state,
    answers: () => receivedAnswers,
  };
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

test("AI feedback shares one answer only after consent and rejects stale confirmation", async () => {
  const f = fixture();
  const ask = f.learning.ask;
  f.learning.ask = async (...args) => ({ ...await ask(...args), eventId: "receipt" });
  const reports: string[] = [];
  f.learning.report = async (_, __, question) => { reports.push(question); };
  await select(f);
  const answer = await f.flow(text("Как получить доступ?"));
  const confirmation = await f.flow(callback(answer, "Ответ AI неверный"));
  assert.match(confirmation.text!, /Остальная переписка не передаётся/);
  assert.deepEqual(reports, []);
  await f.flow(callback(confirmation, "Отмена"));
  await f.flow(callback(confirmation, "Передать HR"));
  assert.deepEqual(reports, []);
  const nextAnswer = await f.flow(text("Как получить доступ?"));
  const current = await f.flow(callback(nextAnswer, "Ответ AI неверный"));
  const saved = await f.flow(callback(current, "Передать HR"));
  assert.match(saved.text!, /передано HR/);
  assert.deepEqual(reports, ["Как получить доступ?"]);
  assert.equal(f.state()?.aiFeedback, undefined);
  await f.flow(callback(current, "Передать HR"));
  assert.equal(reports.length, 1);
});

test("chat menus select an assigned course without starting or submitting a test", async () => {
  const f = fixture();
  const panel = await select(f);
  assert.match(panel.text!, /Первый день/);
  assert.equal(panel.buttons?.flat().length, 5);
  const progress = await f.flow(callback(panel, "Прогресс"));
  assert.match(progress.text!, /Материалы: 1 из 1/);
  assert.deepEqual(f.counts(), { starts: 0, submits: 0, asks: 0 });
});

test("an older course panel refreshes in place with working actions", async () => {
  const f = fixture();
  const oldPanel = await select(f);
  await f.flow(callback(oldPanel, "Прогресс"));
  const recovered = await f.flow(callback(oldPanel, "Пройти тест"));
  assert.match(recovered.text!, /Первый день.*\n\nМеню обновлено/);
  const tests = await f.flow(callback(recovered, "Пройти тест"));
  assert.match(tests.text!, /Выберите тест/);
  assert.deepEqual(f.counts(), { starts: 0, submits: 0, asks: 0 });
});

test("an expired session opens a usable menu from an old button without starting a test", async () => {
  const f = fixture();
  const panel = await select(f);
  f.expireSession();
  const recovered = await f.flow(callback(panel, "Пройти тест"));
  assert.match(recovered.text!, /Назначено курсов: 1/);
  const selected = await f.flow(callback(recovered, "Первый день"));
  assert.match(selected.text!, /Первый день/);
  assert.deepEqual(f.counts(), { starts: 0, submits: 0, asks: 0 });
});

test("an old quiz answer shows the current question without altering saved answers", async () => {
  const f = fixture();
  const first = await begin(f);
  await f.flow(callback(first, "1"));
  const recovered = await f.flow(callback(first, "2"));
  assert.match(recovered.text!, /Вопрос 2 из 2/);
  assert.deepEqual(f.state()?.quiz?.answers, { q1: 0 });
  const result = await f.flow(callback(recovered, "2"));
  assert.match(result.text!, /Тест пройден/);
  assert.deepEqual(f.answers(), { q1: 0, q2: 1 });
  assert.deepEqual(f.counts(), { starts: 1, submits: 1, asks: 0 });
});

test("an old course button cannot select a different course or use an old source index", async () => {
  const f = fixture([
    { id: "course", title: "Первый день", expiresAt: null },
    { id: "other", title: "Другой", expiresAt: null },
  ]);
  await select(f);
  const answer = await f.flow(text("Как получить доступ?"));
  const menu = await f.flow(callback(answer, "Другой курс"));
  await f.flow(callback(menu, "Другой"));
  f.learning.document = async () => {
    assert.fail("An old source must not be loaded for another course");
  };
  const recovered = await f.flow(callback(answer, "Открыть источник 1"));
  assert.match(recovered.text!, /^Другой\n/);
  assert.equal(f.state()?.courseId, "other");
  assert.deepEqual(f.counts(), { starts: 0, submits: 0, asks: 1 });
});

test("an old menu button can return to current assignments while preserving a quiz draft", async () => {
  const f = fixture();
  const panel = await select(f);
  const tests = await f.flow(callback(panel, "Пройти тест"));
  const confirmation = await f.flow(callback(tests, "Проверка"));
  const first = await f.flow(callback(confirmation, "Начать или продолжить"));
  await f.flow(callback(first, "1"));
  const recovered = await f.flow(callback(panel, "Другой курс"));
  assert.match(recovered.text!, /Назначено курсов: 1/);
  assert.deepEqual(f.state()?.quiz?.answers, { q1: 0 });
  assert.equal(f.state()?.quizPaused, true);
  assert.deepEqual(f.counts(), { starts: 1, submits: 0, asks: 0 });
});

test("an old panel cannot recover revoked courses or an unlinked profile", async () => {
  const f = fixture();
  const panel = await select(f);
  await f.flow(text("курсы"));
  f.revoke();
  const recovered = await f.flow(callback(panel, "Пройти тест"));
  assert.match(recovered.text!, /Назначенных курсов пока нет/);
  assert.equal(f.state()?.quiz, undefined);
  f.unlink();
  const unlinked = await f.flow(callback(panel, "Прогресс"));
  assert.match(unlinked.text!, /Как начать обучение/);
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
  const resumed = await f.flow(callback(paused, "Продолжить: Проверка"));
  assert.match(resumed.text!, /Вопрос 2 из 2/);
  assert.match(
    (await f.flow(text("Как получить доступ?"))).text!,
    /Вопрос 2 из 2/,
  );
  assert.equal(f.counts().asks, 0);
});

test("uncertain assessment submission preserves the exact answers for idempotent recovery", async () => {
  const f = fixture();
  const first = await begin(f);
  const second = await f.flow(callback(first, "1"));
  const submit = f.learning.submit;
  f.learning.submit = async () => {
    throw new ChatLearningError("UNAVAILABLE");
  };
  assert.match((await f.flow(callback(second, "1"))).text!, /недоступно/);
  assert.deepEqual(f.state()?.quiz?.answers, { q1: 0, q2: 0 });
  f.learning.submit = submit;
  const list = await f.flow(text("тест"));
  assert.match((await f.flow(callback(list, "Продолжить: Проверка"))).text!, /Тест пройден/);
  assert.equal(f.counts().starts, 1);
});

test("material prerequisite is explained and no chat attempt is fabricated", async () => {
  const f = fixture();
  f.learning.start = async () => {
    throw new ChatLearningError("MATERIAL_REQUIRED");
  };
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
  assert.match(reply.text!, /Как начать обучение/);
  assert.equal(f.state(), null);
  assert.equal(f.counts().asks, 0);
});

test("access is rechecked after AI before returning private text or sources", async () => {
  const f = fixture();
  await select(f);
  const ask = f.learning.ask;
  f.learning.ask = async (...args) => {
    f.revoke();
    return ask(...args);
  };
  const answer = await f.flow(text("Как получить доступ?"));
  assert.doesNotMatch(answer.text!, /Подтверждённый/);
  assert.equal(f.state()?.documentIds, undefined);
});

test("sensitive questions are not sent to AI, and oversized quiz messages stay in Mini App", () => {
  for (const value of [
    "123456",
    "мой код 123456",
    "me@example.com",
    "+7 999 123-45-67",
    "пароль=secret",
  ]) {
    assert.equal(isSafeChatQuestion(value), false);
  }
  assert.equal(isSafeChatQuestion("Как устроено обучение?"), true);
  assert.equal(isShortChatQuiz(questions), true);
  assert.equal(
    isShortChatQuiz([{ ...questions[0], prompt: "x".repeat(3000) }]),
    false,
  );
  assert.equal(
    isShortChatQuiz(Array.from({ length: 11 }, () => questions[0])),
    false,
  );
});

test("intake accepts only fresh private messages and callbacks belonging to the same user", () => {
  const now = Date.now();
  const event = {
    update_type: "message_created",
    timestamp: now,
    message: {
      sender: { user_id: 123, is_bot: false, name: "private" },
      recipient: { chat_type: "dialog", user_id: 99 },
      body: { mid: "mid", text: "мой код 123456" },
    },
  };
  const parsed = parseChatEvent(event, now);
  assert.notEqual(parsed, "ignored");
  assert.ok(parsed !== "ignored" && parsed.input.type === "text");
  assert.equal(parsed.input.text, "помощь");
  assert.ok(!JSON.stringify(parsed).includes("private"));
  assert.equal(
    parseChatEvent({ ...event, timestamp: now - 300_001 }, now),
    "ignored",
  );
  const cb = {
    ...event,
    update_type: "message_callback",
    callback: {
      callback_id: "cb",
      user: { user_id: 123, is_bot: false },
      payload: "chat:0000000000000001:progress",
    },
  };
  assert.equal(parseChatEvent(cb, now), "ignored");
  cb.message.recipient.user_id = 123;
  assert.notEqual(parseChatEvent(cb, now), "ignored");
  cb.message.recipient.chat_type = "chat";
  assert.equal(parseChatEvent(cb, now), "ignored");
});

test("confirmation returns to the test list, including its original page", async () => {
  const f = fixture();
  f.learning.course = async () => ({
    ...course,
    quizzes: Array.from({ length: 12 }, (_, index) => ({
      ...course.quizzes[0],
      id: `quiz-${index}`,
      title: `Проверка ${index + 1}`,
    })),
  });
  const panel = await select(f);
  const firstPage = await f.flow(callback(panel, "Пройти тест"));
  assert.match(firstPage.text!, /Страница 1 из 3/);
  const secondPage = await f.flow(callback(firstPage, "Ещё тесты"));
  assert.match(secondPage.text!, /Страница 2 из 3/);
  const confirmation = await f.flow(callback(secondPage, "Проверка 7"));
  const returned = await f.flow(callback(confirmation, "Назад"));
  assert.match(returned.text!, /Страница 2 из 3/);
  assert.ok(callback(returned, "Проверка 7"));
  assert.equal(f.counts().starts, 0);
});

test("a paused quiz allows progress and AI and resumes without spending another attempt", async () => {
  const f = fixture();
  const first = await begin(f);
  const second = await f.flow(callback(first, "1"));
  const paused = await f.flow(callback(second, "Продолжить позже"));
  assert.match(paused.text!, /Выберите тест/);
  assert.equal(paused.buttons?.flat().length, 2);
  const panel = await f.flow(callback(paused, "Назад"));
  const progress = await f.flow(callback(panel, "Прогресс"));
  assert.match(progress.text!, /Материалы/);
  await f.flow(callback(progress, "Спросить AI"));
  const answer = await f.flow(text("Как устроено обучение?"));
  assert.equal(f.counts().asks, 1);
  const resumed = await f.flow(callback(answer, "Продолжить тест"));
  assert.match(resumed.text!, /Вопрос 2 из 2/);
  assert.deepEqual(f.state()?.quiz?.answers, { q1: 0 });
  assert.equal(f.counts().starts, 1);
});

test("start menu and another course do not discard or misattribute a paused draft", async () => {
  const f = fixture([
    { id: "course", title: "Первый день", expiresAt: null },
    { id: "other", title: "Другой", expiresAt: null },
  ]);
  const first = await begin(f);
  await f.flow(callback(first, "1"));
  const menu = await f.flow(text("/start"));
  const other = await f.flow(callback(menu, "Другой"));
  assert.ok(
    !other.buttons?.flat().some((button) => button.text === "Продолжить тест"),
  );
  const again = await f.flow(callback(other, "Другой курс"));
  const panel = await f.flow(callback(again, "Первый день"));
  const resumed = await f.flow(callback(panel, "Продолжить тест"));
  assert.match(resumed.text!, /Вопрос 2 из 2/);
  assert.equal(f.state()?.quiz?.courseId, "course");
  assert.equal(f.counts().starts, 1);
});

test("one hundred courses use five buttons per page and search only current assignments", async () => {
  const f = fixture(
    Array.from({ length: 100 }, (_, index) => ({
      id: `course-${index}`,
      title: `Курс ${index + 1}`,
      expiresAt: null,
    })),
  );
  let page = await f.flow(text("/start"));
  assert.match(page.text!, /100\. Страница 1 из 20/);
  for (let index = 0; index < 20; index++) {
    const courseButtons = page
      .buttons!.flat()
      .filter(
        (button) =>
          button.type === "callback" && button.payload.includes(":course:"),
      );
    assert.equal(courseButtons.length, 5);
    assert.equal(courseButtons[0].text, `Курс ${index * 5 + 1}`);
    if (index < 19) page = await f.flow(callback(page, "Ещё курсы"));
  }
  await f.flow(callback(page, "Найти курс"));
  const found = await f.flow(text("КУРС 99"));
  assert.match(found.text!, /Найдено курсов: 1/);
  assert.ok(callback(found, "Курс 99"));
  const empty = await f.flow(text("чужой курс"));
  assert.match(empty.text!, /не найден/);
  assert.equal(f.counts().asks, 0);
});

test("answer buttons do not reload an entire course; sources and AI render Markdown as text", async () => {
  const f = fixture();
  const first = await begin(f);
  const reads = f.courseReads();
  const second = await f.flow(callback(first, "1"));
  assert.equal(f.courseReads(), reads);
  const paused = await f.flow(callback(second, "Продолжить позже"));
  const panel = await f.flow(callback(paused, "Назад"));
  await f.flow(callback(panel, "Спросить AI"));
  const ask = f.learning.ask;
  f.learning.ask = async (...args) => ({
    ...(await ask(...args)),
    answer: "## Доступ\n\n**Обратитесь к HR**.",
  });
  f.learning.document = async () => ({
    title: "Правила",
    contentText: "# Правила\n\n## Доступ\n- Получите код",
  });
  const answer = await f.flow(text("Как получить доступ?"));
  assert.doesNotMatch(answer.text!, /##|\*\*/);
  const source = await f.flow(callback(answer, "Открыть источник 1"));
  assert.doesNotMatch(source.text!, /# /);
  assert.match(source.text!, /Получите код/);
});

test("a quiz completed in Mini App is not resumed or submitted again in chat", async () => {
  const f = fixture();
  const first = await begin(f);
  const paused = await f.flow(callback(first, "Продолжить позже"));
  f.learning.course = async () => ({
    ...course,
    quizzes: [{ ...course.quizzes[0], status: "PASSED" }],
  });
  const result = await f.flow(callback(paused, "Продолжить: Проверка"));
  assert.match(result.text!, /уже пройден/);
  assert.equal(f.state()?.quiz, undefined);
  assert.equal(f.counts().submits, 0);
});

test("progress after assignment expiry shows the empty menu, not a course search", async () => {
  const f = fixture();
  await select(f);
  f.revoke();
  const result = await f.flow(text("прогресс"));
  assert.match(result.text!, /Назначенных курсов пока нет/);
  assert.equal(f.state()?.courseSearch, "");
});

test("test-list navigation never submits prepared answers and has no duplicate resume button", async () => {
  const f = fixture();
  const first = await begin(f);
  const second = await f.flow(callback(first, "1"));
  f.learning.submit = async () => { throw new ChatLearningError("UNAVAILABLE"); };
  await f.flow(callback(second, "2"));
  const list = await f.flow(text("тест"));
  assert.deepEqual(list.buttons?.flat().map((button) => button.text), ["Продолжить: Проверка", "Назад"]);
  const panel = await f.flow(callback(list, "Назад"));
  assert.match(panel.text!, /Выберите действие/);
  const returned = await f.flow(callback(panel, "Другие тесты"));
  assert.match(returned.text!, /Выберите тест/);
  assert.deepEqual(f.state()?.quiz?.answers, { q1: 0, q2: 1 });
  assert.deepEqual(f.counts(), { starts: 1, submits: 0, asks: 0 });
});

test("menu aliases and help expose navigation and preserve a paused quiz", async () => {
  const f = fixture();
  const first = await begin(f);
  await f.flow(callback(first, "1"));
  for (const command of ["Меню", "/menu", "/start", "/courses", "/help"]) {
    const menu = await f.flow(text(command));
    assert.match(menu.text!, /Меню|меню/);
    assert.ok(callback(menu, "Первый день"));
    assert.deepEqual(f.state()?.quiz?.answers, { q1: 0 });
  }
  assert.equal(f.counts().starts, 1);
});

test("unlinked users receive the code issuer and exact first-entry steps", async () => {
  const f = fixture();
  f.unlink();
  const result = await f.flow(text("/start"));
  assert.match(result.text!, /HR или руководителя/);
  assert.match(result.text!, /администратор/);
  assert.match(result.text!, /не в чат/);
  assert.match(result.text!, /Меню/);
  assert.equal(result.buttons?.flat()[0].type, "open_app");
  assert.equal(f.counts().starts, 0);
});

test("passed repeatable quizzes stay available and can resume even beyond their original limit", async () => {
  const f = fixture();
  f.learning.course = async () => ({
    ...course,
    quizzes: [{ ...course.quizzes[0], status: "PASSED", attemptsUsed: 10, repeatable: true }],
  });
  const panel = await select(f);
  const list = await f.flow(callback(panel, "Пройти тест"));
  const confirmation = await f.flow(callback(list, "Проверка"));
  assert.match(confirmation.text!, /без ограничений/);
  const first = await f.flow(callback(confirmation, "Начать или продолжить"));
  const paused = await f.flow(callback(first, "Продолжить позже"));
  const resumed = await f.flow(callback(paused, "Продолжить: Проверка"));
  assert.match(resumed.text!, /Вопрос 1 из 2/);
  const second = await f.flow(callback(resumed, "1"));
  const result = await f.flow(callback(second, "1"));
  assert.match(result.text!, /Тест пройден/);
  assert.deepEqual(f.counts(), { starts: 1, submits: 1, asks: 0 });
});
