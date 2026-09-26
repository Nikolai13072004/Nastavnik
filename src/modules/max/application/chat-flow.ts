import type { ChatAccess, ChatInput, ChatLearning, ChatReply, ChatSessionRepository } from "./chat";
import { ChatLearningError, isSafeChatQuestion } from "./chat";
import { chatButton, chatCourseActions, chatCourseMenu, chatQuizQuestion } from "./chat-messages";
import { isShortChatQuiz } from "./quiz-delivery";

export function createChatFlow(deps: {
  access: ChatAccess; sessions: ChatSessionRepository; learning: ChatLearning;
  botUsername: string; version(): string;
}) {
  return async (maxUserId: string, input: ChatInput): Promise<ChatReply> => {
    const identity = await deps.access.identity(maxUserId);
    const courses = identity ? await deps.access.courses(identity) : null;
    if (!identity || !courses) {
      await deps.sessions.clear(maxUserId);
      return { text: "Сначала свяжите профиль: получите свой код у HR и введите его в приложении. Код в чат не отправляйте.",
        buttons: [[{ type: "open_app", text: "Связать профиль", web_app: deps.botUsername }]] };
    }
    let state = await deps.sessions.load(identity) ?? { version: deps.version() };
    const save = async () => {
      state = { ...state, version: deps.version() };
      await deps.sessions.save(identity, state);
    };
    const actions = () => chatCourseActions(state, deps.botUsername);
    const menu = async (page = 0) => {
      state = { version: state.version, courseIds: courses.map((course) => course.id), aiRequestedAt: state.aiRequestedAt };
      await save();
      return chatCourseMenu(state, courses, deps.botUsername, page);
    };
    let action = "text";
    let index = -1;
    if (input.type === "callback") {
      const parts = input.payload.split(":");
      if (parts[1] !== state.version) return { notification: "Кнопка устарела. Используйте последнее меню или напишите «Курсы»." };
      action = parts[2];
      index = parts.length === 4 ? Number(parts[3]) : -1;
    } else {
      const command = input.text.toLowerCase().replace(/^\//, "").trim();
      if (["курсы", "меню", "start", "courses", "помощь", "help", "отмена", "cancel"].includes(command)) return menu();
      if (["прогресс", "progress"].includes(command)) action = "progress";
      if (["тест", "test"].includes(command)) action = "tests";
    }
    if (action === "menu") return menu();
    if (action === "page") {
      return menu(Number.isInteger(index) && index >= 0 && index * 5 < courses.length ? index : 0);
    }
    if (action === "course") {
      const courseId = state.courseIds?.[index];
      if (!courseId || !courses.some((course) => course.id === courseId)) return menu();
      state = { version: state.version, courseId, aiRequestedAt: state.aiRequestedAt };
      await save();
      return { text: `${courses.find((course) => course.id === courseId)!.title}\n\nНапишите вопрос по документам курса или выберите действие.`, buttons: actions() };
    }
    if (!state.courseId || !courses.some((course) => course.id === state.courseId)) return menu();
    const courseId = state.courseId;
    try {
      const course = await deps.learning.course(identity, courseId);
      if (state.quiz && !["answer", "leave"].includes(action)) {
        if (Object.keys(state.quiz.answers).length === state.quiz.questions.length) {
          action = "finish";
        } else {
          await save();
          return chatQuizQuestion(state);
        }
      }
      if (action === "leave") {
        await save();
        return { text: "Ответы сохранены на 30 минут. Начатая попытка остаётся в истории. Чтобы продолжить, напишите «Тест». Для другого курса напишите «Курсы».",
          buttons: [[chatButton(state, "Продолжить тест", "resume")]] };
      }
      if (action === "answer" && state.quiz) {
        const question = state.quiz.questions[Object.keys(state.quiz.answers).length];
        if (!question || !Number.isInteger(index) || index < 0 || index >= question.options.length) {
          return { notification: "Выберите вариант в текущем вопросе." };
        }
        state.quiz.answers[question.id] = index;
        // Save before submission: an uncertain HTTP response can be recovered with the same answers.
        await save();
        if (Object.keys(state.quiz.answers).length < state.quiz.questions.length) return chatQuizQuestion(state);
        action = "finish";
      }
      if (action === "finish" && state.quiz) {
        const result = await deps.learning.submit(identity, courseId, state.quiz);
        state = { version: state.version, courseId, aiRequestedAt: state.aiRequestedAt };
        await save();
        return { text: `${result.outcome === "PASSED" ? "Тест пройден." : "Тест завершён, проходной балл пока не набран."}\nПравильных ответов: ${result.correctAnswers} из ${result.totalQuestions}.\nРезультат сохранён в истории обучения.`, buttons: actions() };
      }
      if (action === "progress") {
        await save();
        const completed = course.materials.filter((material) => material.completed).length;
        const quizzes = course.quizzes.slice(0, 5).map((quiz) =>
          `${quiz.title.slice(0, 80)}: ${quiz.status === "PASSED" ? "пройден" : "ещё не пройден"}, попыток ${quiz.attemptsUsed} из ${quiz.maxAttempts}`).join("\n");
        return { text: `${course.title.slice(0, 180)}\nМатериалы: ${completed} из ${course.materials.length}.\n${quizzes || "Тестов нет."}\n${course.completed ? "Курс завершён." : "Курс ещё не завершён."}`, buttons: actions() };
      }
      if (action === "tests") {
        const quizzes = course.quizzes.filter((quiz) => quiz.chatSupported && quiz.status !== "PASSED").slice(0, 5);
        state.quizIds = quizzes.map((quiz) => quiz.id);
        await save();
        return { text: quizzes.length ? "Выберите короткий тест. Начало новой попытки нужно подтвердить. Уже начатый тест можно продолжить."
          : "Коротких непройденных тестов нет. Все тесты доступны в приложении.",
          buttons: [...quizzes.map((quiz, position) => [chatButton(state, quiz.title.slice(0, 80), "confirm", position)]), ...actions()] };
      }
      if (action === "confirm") {
        const quiz = course.quizzes.find((item) => item.id === state.quizIds?.[index]);
        if (!quiz?.chatSupported) return { notification: "Тест недоступен. Обновите меню." };
        state.quizIds = [quiz.id];
        await save();
        return { text: `${quiz.title.slice(0, 180)}\nВопросов: ${quiz.questionCount}. Попыток использовано: ${quiz.attemptsUsed} из ${quiz.maxAttempts}.\n\nНовая попытка учитывается сразу после начала. Незавершённая попытка будет продолжена, а не создана заново.`,
          buttons: [[chatButton(state, "Начать или продолжить", "start", 0)], [chatButton(state, "Вернуться", "progress")]] };
      }
      if (action === "start") {
        const quiz = course.quizzes.find((item) => item.id === state.quizIds?.[index]);
        if (!quiz?.chatSupported) return { notification: "Тест недоступен. Обновите меню." };
        const started = await deps.learning.start(identity, courseId, quiz.id);
        if (!isShortChatQuiz(started.questions)) {
          return { text: "Тест изменился. Продолжите начатую попытку в приложении.", buttons: actions() };
        }
        state.quiz = { id: quiz.id, ...started, answers: {} };
        await save();
        return chatQuizQuestion(state);
      }
      if (action === "source") {
        const documentId = state.documentIds?.[index];
        if (!documentId) return { notification: "Источник недоступен. Задайте вопрос ещё раз." };
        const document = await deps.learning.document(identity, courseId, documentId);
        await save();
        return { text: `${document.title.slice(0, 180)}\n\n${document.contentText.slice(0, 2200)}${document.contentText.length > 2200 ? "\n\nПолный документ доступен в материалах курса." : ""}`, buttons: actions() };
      }
      if (action === "text" && input.type === "text") {
        if (!isSafeChatQuestion(input.text)) {
          return { text: "Задайте вопрос по документам курса, от 3 до 500 символов. Не отправляйте коды, пароли, телефоны или личные данные.", buttons: actions() };
        }
        const recent = (state.aiRequestedAt ?? []).filter((time) => time > Date.now() - 60_000);
        if (recent.length >= 3) {
          return { text: "Подождите минуту перед следующим вопросом AI. Материалы, прогресс и тесты доступны без ожидания.", buttons: actions() };
        }
        state.aiRequestedAt = [...recent, Date.now()];
        await save();
        const answer = await deps.learning.ask(identity, courseId, input.text);
        const current = await deps.access.courses(identity);
        if (!current?.some((item) => item.id === courseId)) return menu();
        const linkedSources = answer.sources.filter((source) => source.courseDocumentId).slice(0, 3);
        state.documentIds = linkedSources.map((source) => source.courseDocumentId!);
        await save();
        const sources = linkedSources.map((source, position) => `Источник ${position + 1}: ${source.title.slice(0, 120)}`).join("\n");
        return { text: `${answer.answer.slice(0, 2000)}${sources ? `\n\n${sources}` : ""}`,
          buttons: [...state.documentIds.map((_, position) => [chatButton(state, `Открыть источник ${position + 1}`, "source", position)]), ...actions()] };
      }
      await save();
      return { text: "Напишите вопрос по документам выбранного курса. Ответ будет с проверенным источником; если подтверждения нет, AI сообщит об этом.", buttons: actions() };
    } catch (error) {
      const code = error instanceof ChatLearningError ? error.code : "UNAVAILABLE";
      const text = code === "MATERIAL_REQUIRED" ? "Сначала изучите обязательные материалы в приложении. Попытка теста не началась."
        : ["FORBIDDEN", "SESSION_REVOKED", "NOT_FOUND"].includes(code) ? "Доступ изменился. Напишите «Курсы», чтобы обновить меню."
          : "Сейчас действие недоступно. Попробуйте позже или откройте приложение. Начатая попытка не создаётся повторно.";
      return { text, buttons: actions() };
    }
  };
}
