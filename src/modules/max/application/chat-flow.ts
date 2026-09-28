import type {
  ChatAccess,
  ChatInput,
  ChatLearning,
  ChatReply,
  ChatSessionRepository,
} from "./chat";
import { ChatLearningError, isSafeChatQuestion } from "./chat";
import {
  chatButton,
  chatCourseActions,
  chatCourseMenu,
  chatQuizQuestion,
} from "./chat-messages";
import { isShortChatQuiz } from "./quiz-delivery";
import { readableDocumentText } from "@/lib/document-text";

export function createChatFlow(deps: {
  access: ChatAccess;
  sessions: ChatSessionRepository;
  learning: ChatLearning;
  botUsername: string;
  version(): string;
}) {
  return async (maxUserId: string, input: ChatInput): Promise<ChatReply> => {
    const identity = await deps.access.identity(maxUserId);
    const courses = identity ? await deps.access.courses(identity) : null;
    if (!identity || !courses) {
      await deps.sessions.clear(maxUserId);
      return {
        text: "Как начать обучение:\n1. Попросите личный код у HR или руководителя. Для проверки стенда код выдаёт его администратор.\n2. Нажмите «Связать профиль» и введите код там, не в чат.\n3. После привязки напишите «Меню» или /start: появятся курсы и действия.",
        buttons: [
          [
            {
              type: "open_app",
              text: "Связать профиль",
              web_app: deps.botUsername,
            },
          ],
        ],
      };
    }
    let state = (await deps.sessions.load(identity)) ?? {
      version: deps.version(),
    };
    // Keep drafts from earlier releases bound to their original course.
    if (state.quiz && !state.quiz.courseId)
      state.quiz.courseId = state.courseId;
    if (
      state.quiz &&
      !courses.some((course) => course.id === state.quiz?.courseId)
    )
      delete state.quiz;
    const save = async () => {
      state = { ...state, version: deps.version() };
      await deps.sessions.save(identity, state);
    };
    const actions = () => chatCourseActions(state, deps.botUsername);
    const menu = async (page = 0, search = "") => {
      const matches = search
        ? courses.filter((course) =>
            course.title
              .toLocaleLowerCase("ru-RU")
              .includes(search.toLocaleLowerCase("ru-RU")),
          )
        : courses;
      const validPage =
        Number.isInteger(page) && page >= 0 && page * 5 < matches.length
          ? page
          : 0;
      state = {
        version: state.version,
        courseIds: matches.map((course) => course.id),
        courseSearch: search,
        aiRequestedAt: state.aiRequestedAt,
        quiz: state.quiz,
        quizPaused: true,
      };
      await save();
      return chatCourseMenu(state, matches, deps.botUsername, validPage);
    };
    let action = "text";
    let index = -1;
    if (input.type === "callback") {
      const parts = input.payload.split(":");
      if (parts[1] !== state.version) {
        if (
          parts[2] === "menu" ||
          !state.courseId ||
          !courses.some((course) => course.id === state.courseId)
        ) {
          return {
            ...(await menu()),
            notification: "Меню обновлено. Выберите курс.",
          };
        }
        // Recover this message without applying an action from an older screen.
        const quiz = state.quiz;
        if (
          quiz?.courseId === state.courseId &&
          !state.quizPaused &&
          Object.keys(quiz.answers).length < quiz.questions.length
        ) {
          return {
            ...chatQuizQuestion(state),
            notification: "Показан текущий вопрос. Старый ответ не засчитан.",
          };
        }
        return {
          text: `${courses.find((course) => course.id === state.courseId)!.title.slice(0, 180)}\n\nМеню обновлено. Выберите действие для этого курса.`,
          buttons: actions(),
          notification: "Меню обновлено.",
        };
      }
      action = parts[2];
      index = parts.length === 4 ? Number(parts[3]) : -1;
    } else {
      const command = input.text.toLowerCase().replace(/^\//, "").trim();
      if (["помощь", "help"].includes(command)) {
        return {
          ...(await menu()),
          text: "Выберите курс кнопкой ниже. В чате доступны вопросы AI, прогресс и короткие тесты. Для чтения материалов нажмите «Материалы и документы» в меню курса.\n\nМеню можно открыть в любой момент: отправьте «Меню», «Курсы» или /start. Незавершённый тест сохранится. Код привязки вводится только в мини-приложении, не в чате.",
        };
      }
      if (
        [
          "курсы",
          "меню",
          "start",
          "courses",
          "menu",
          "отмена",
          "cancel",
        ].includes(command)
      )
        return menu();
      if (["прогресс", "progress"].includes(command)) action = "progress";
      if (["тест", "test"].includes(command)) action = "tests";
    }
    if (action === "menu") return menu();
    if (action === "page") {
      return menu(index, state.courseSearch);
    }
    if (action === "search") {
      const reply = await menu();
      return {
        ...reply,
        text: "Напишите часть названия курса. Поиск только по вашим назначениям. Для отмены напишите «Курсы».",
      };
    }
    if (action === "course") {
      const courseId = state.courseIds?.[index];
      if (!courseId || !courses.some((course) => course.id === courseId))
        return menu();
      state = {
        version: state.version,
        courseId,
        aiRequestedAt: state.aiRequestedAt,
        quiz: state.quiz,
        quizPaused: true,
      };
      await save();
      return {
        text: `${courses.find((course) => course.id === courseId)!.title}\n\nНапишите вопрос по документам курса или выберите действие.`,
        buttons: actions(),
      };
    }
    if (
      !state.courseId ||
      !courses.some((course) => course.id === state.courseId)
    ) {
      return menu(
        0,
        action === "text" &&
          input.type === "text" &&
          isSafeChatQuestion(input.text)
          ? input.text.trim()
          : "",
      );
    }
    const courseId = state.courseId;
    const currentQuiz =
      state.quiz?.courseId === courseId ? state.quiz : undefined;
    try {
      if (action === "report" && state.aiFeedback && deps.learning.report) {
        await save();
        return {
          text: "Передать HR этот вопрос, ответ AI и источники для проверки? Остальная переписка не передаётся.",
          buttons: [[chatButton(state, "Передать HR", "reportconfirm"), chatButton(state, "Отмена", "back")]],
        };
      }
      if (action === "reportconfirm" && state.aiFeedback && deps.learning.report) {
        await deps.learning.report(identity, courseId, state.aiFeedback.question, state.aiFeedback.result);
        delete state.aiFeedback;
        await save();
        return { text: "Сообщение передано HR для проверки.", buttons: actions() };
      }
      if (currentQuiz && action === "resume") {
        const course = await deps.learning.course(identity, courseId);
        if (
          course.quizzes.some(
            (quiz) => quiz.id === currentQuiz.id && quiz.status === "PASSED" && !quiz.repeatable,
          )
        ) {
          delete state.quiz;
          await save();
          return {
            text: "Этот тест уже пройден. Результат сохранён в истории обучения.",
            buttons: actions(),
          };
        }
        state.quizPaused = false;
        if (
          Object.keys(currentQuiz.answers).length ===
          currentQuiz.questions.length
        ) {
          action = "finish";
        } else {
          await save();
          return chatQuizQuestion(state);
        }
      }
      if (currentQuiz && !state.quizPaused && action === "text") {
        if (
          Object.keys(currentQuiz.answers).length ===
          currentQuiz.questions.length
        )
          action = "finish";
        else {
          await save();
          return chatQuizQuestion(state);
        }
      }
      if (action === "leave") {
        state.quizPaused = true;
        action = "tests";
      }
      if (action === "panel") {
        state.quizPaused = true;
        await save();
        return {
          text: `${courses.find((course) => course.id === courseId)!.title}\n\nВыберите действие для этого курса.`,
          buttons: actions(),
        };
      }
      if (action === "answer") {
        if (!currentQuiz || state.quizPaused)
          return { notification: "Сначала продолжите тест из меню курса." };
        const question =
          currentQuiz.questions[Object.keys(currentQuiz.answers).length];
        if (
          !question ||
          !Number.isInteger(index) ||
          index < 0 ||
          index >= question.options.length
        ) {
          return { notification: "Выберите вариант в текущем вопросе." };
        }
        currentQuiz.answers[question.id] = index;
        // Save before submission: an uncertain HTTP response can be recovered with the same answers.
        await save();
        if (
          Object.keys(currentQuiz.answers).length < currentQuiz.questions.length
        )
          return chatQuizQuestion(state);
        action = "finish";
      }
      if (action === "finish" && currentQuiz) {
        const result = await deps.learning.submit(
          identity,
          courseId,
          currentQuiz,
        );
        state = {
          version: state.version,
          courseId,
          aiRequestedAt: state.aiRequestedAt,
        };
        await save();
        return {
          text: `${result.outcome === "PASSED" ? "Тест пройден." : "Тест завершён, проходной балл пока не набран."}\nПравильных ответов: ${result.correctAnswers} из ${result.totalQuestions}.\nРезультат сохранён в истории обучения.`,
          buttons: actions(),
        };
      }
      if (action === "progress") {
        const course = await deps.learning.course(identity, courseId);
        state.quizPaused = true;
        await save();
        const completed = course.materials.filter(
          (material) => material.completed,
        ).length;
        const passed = course.quizzes.filter(
          (quiz) => quiz.status === "PASSED",
        ).length;
        return {
          text: `${course.title.slice(0, 180)}\nМатериалы: ${completed} из ${course.materials.length}.\nТесты: ${passed} из ${course.quizzes.length} пройдены.\n${course.completed ? "Курс завершён." : "Курс ещё не завершён."}`,
          buttons: actions(),
        };
      }
      if (action === "tests" || action === "testpage") {
        const course = await deps.learning.course(identity, courseId);
        const quizzes = course.quizzes.filter(
          (quiz) => quiz.chatSupported && (quiz.status !== "PASSED" || quiz.repeatable),
        );
        const page = action === "testpage" ? index : (state.quizPage ?? 0);
        const validPage =
          Number.isInteger(page) && page >= 0 && page * 5 < quizzes.length
            ? page
            : 0;
        const start = validPage * 5;
        state.quizPage = validPage;
        state.quizIds = quizzes.map((quiz) => quiz.id);
        state.quizPaused = true;
        await save();
        const navigation = [];
        if (validPage > 0)
          navigation.push(
            chatButton(state, "Предыдущие тесты", "testpage", validPage - 1),
          );
        if (start + 5 < quizzes.length)
          navigation.push(
            chatButton(state, "Ещё тесты", "testpage", validPage + 1),
          );
        return {
          text: `${currentQuiz ? "Ответы сохранены на 30 минут. Можно продолжить тест или выбрать другое действие.\n\n" : ""}${
            quizzes.length
              ? `Выберите тест. Страница ${validPage + 1} из ${Math.ceil(quizzes.length / 5)}. Начало новой попытки нужно подтвердить.`
              : "Коротких непройденных тестов нет. Все тесты доступны в приложении."
          }`,
          buttons: [
            ...quizzes
              .slice(start, start + 5)
              .map((quiz, position) => [
                currentQuiz?.id === quiz.id
                  ? chatButton(state, `Продолжить: ${quiz.title.slice(0, 65)}`, "resume")
                  : chatButton(state, quiz.title.slice(0, 80), "confirm", start + position),
              ]),
            ...(navigation.length ? [navigation] : []),
            [chatButton(state, "Назад", "panel")],
          ],
        };
      }
      if (action === "confirm") {
        const course = await deps.learning.course(identity, courseId);
        const quiz = course.quizzes.find(
          (item) => item.id === state.quizIds?.[index],
        );
        if (!quiz?.chatSupported)
          return { notification: "Тест недоступен. Обновите меню." };
        await save();
        return {
          text: `${quiz.title.slice(0, 180)}\nВопросов: ${quiz.questionCount}. ${quiz.repeatable
            ? "Тренировочный тест: повторять можно без ограничений, даже после сдачи. Лучший результат сохраняется."
            : `Попыток использовано: ${quiz.attemptsUsed} из ${quiz.maxAttempts}.`}\n\nНезавершённая попытка будет продолжена, а не создана заново.${state.quiz && state.quiz.id !== quiz.id ? " Черновик ответов другого теста в чате будет заменён." : ""}`,
          buttons: [
            [chatButton(state, "Начать или продолжить", "start", index)],
            [chatButton(state, "Назад", "tests")],
          ],
        };
      }
      if (action === "start") {
        const course = await deps.learning.course(identity, courseId);
        const quiz = course.quizzes.find(
          (item) => item.id === state.quizIds?.[index],
        );
        if (!quiz?.chatSupported)
          return { notification: "Тест недоступен. Обновите меню." };
        if (quiz.status === "PASSED" && !quiz.repeatable) {
          if (currentQuiz?.id === quiz.id) delete state.quiz;
          await save();
          return {
            text: "Этот тест уже пройден. Результат сохранён в истории обучения.",
            buttons: actions(),
          };
        }
        if (currentQuiz?.id === quiz.id) {
          state.quizPaused = false;
          await save();
          if (
            Object.keys(currentQuiz.answers).length <
            currentQuiz.questions.length
          )
            return chatQuizQuestion(state);
          return {
            text: "Ответы уже подготовлены. Нажмите «Продолжить тест», чтобы сохранить результат.",
            buttons: actions(),
          };
        }
        const started = await deps.learning.start(identity, courseId, quiz.id);
        if (!isShortChatQuiz(started.questions)) {
          return {
            text: "Тест изменился. Продолжите начатую попытку в приложении.",
            buttons: actions(),
          };
        }
        state.quiz = { id: quiz.id, courseId, ...started, answers: {} };
        state.quizPaused = false;
        await save();
        return chatQuizQuestion(state);
      }
      if (action === "source") {
        const documentId = state.documentIds?.[index];
        if (!documentId)
          return {
            notification: "Источник недоступен. Задайте вопрос ещё раз.",
          };
        const document = await deps.learning.document(
          identity,
          courseId,
          documentId,
        );
        const content = readableDocumentText(document.contentText);
        await save();
        return {
          text: `${document.title.slice(0, 180)}\n\n${content.slice(0, 2200)}${content.length > 2200 ? "\n\nПолный документ доступен в материалах курса." : ""}`,
          buttons: actions(),
        };
      }
      if (action === "text" && input.type === "text") {
        if (!isSafeChatQuestion(input.text)) {
          return {
            text: "Задайте вопрос по документам курса, от 3 до 500 символов. Не отправляйте коды, пароли, телефоны или личные данные.",
            buttons: actions(),
          };
        }
        const recent = (state.aiRequestedAt ?? []).filter(
          (time) => time > Date.now() - 60_000,
        );
        if (recent.length >= 3) {
          return {
            text: "Подождите минуту перед следующим вопросом AI. Материалы, прогресс и тесты доступны без ожидания.",
            buttons: actions(),
          };
        }
        state.aiRequestedAt = [...recent, Date.now()];
        await save();
        const answer = await deps.learning.ask(identity, courseId, input.text);
        state.aiFeedback = answer.eventId ? { question: input.text, result: answer } : undefined;
        const current = await deps.access.courses(identity);
        if (!current?.some((item) => item.id === courseId)) return menu();
        const linkedSources = answer.sources
          .filter((source) => source.courseDocumentId)
          .slice(0, 3);
        state.documentIds = linkedSources.map(
          (source) => source.courseDocumentId!,
        );
        await save();
        const sources = linkedSources
          .map(
            (source, position) =>
              `Источник ${position + 1}: ${source.title.slice(0, 120)}`,
          )
          .join("\n");
        return {
          text: `${readableDocumentText(answer.answer).slice(0, 2000)}${sources ? `\n\n${sources}` : ""}`,
          buttons: [
            ...(state.aiFeedback && deps.learning.report ? [[chatButton(state, "Ответ AI неверный", "report")]] : []),
            ...state.documentIds.map((_, position) => [
              chatButton(
                state,
                `Открыть источник ${position + 1}`,
                "source",
                position,
              ),
            ]),
            ...actions(),
          ],
        };
      }
      state.quizPaused = true;
      await save();
      return {
        text: "Напишите вопрос по документам выбранного курса. Ответ будет с проверенным источником; если подтверждения нет, AI сообщит об этом.",
        buttons: actions(),
      };
    } catch (error) {
      const code =
        error instanceof ChatLearningError ? error.code : "UNAVAILABLE";
      const text =
        code === "MATERIAL_REQUIRED"
          ? "Сначала изучите обязательные материалы в приложении. Попытка теста не началась."
          : ["FORBIDDEN", "SESSION_REVOKED", "NOT_FOUND"].includes(code)
            ? "Доступ изменился. Напишите «Курсы», чтобы обновить меню."
            : "Сейчас действие недоступно. Попробуйте позже или откройте приложение. Начатая попытка не создаётся повторно.";
      return { text, buttons: actions() };
    }
  };
}
