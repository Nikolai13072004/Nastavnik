import type { ChatButton, ChatReply, ChatState } from "./chat";
import type { MaxCourse } from "./list-courses";

export function chatButton(
  state: ChatState,
  text: string,
  action: string,
  index?: number,
): ChatButton {
  return {
    type: "callback",
    text,
    payload: `chat:${state.version}:${action}${index === undefined ? "" : `:${index}`}`,
  };
}

export function chatCourseActions(
  state: ChatState,
  botUsername: string,
): ChatButton[][] {
  return [
    ...(state.quiz?.courseId === state.courseId
      ? [[chatButton(state, "Продолжить тест", "resume")]]
      : []),
    [
      chatButton(state, "Спросить AI", "ask"),
      chatButton(state, "Прогресс", "progress"),
    ],
    [chatButton(state, "Пройти тест", "tests")],
    [
      {
        type: "open_app",
        text: "Материалы и документы",
        web_app: botUsername,
        payload: `course_${state.courseId}`,
      },
    ],
    [chatButton(state, "Другой курс", "menu")],
  ];
}

export function chatCourseMenu(
  state: ChatState,
  courses: MaxCourse[],
  botUsername: string,
  page = 0,
): ChatReply {
  const start = page * 5;
  const buttons = courses
    .slice(start, start + 5)
    .map((course, offset) => [
      chatButton(state, course.title.slice(0, 80), "course", start + offset),
    ]);
  const navigation: ChatButton[] = [];
  if (page > 0) navigation.push(chatButton(state, "Назад", "page", page - 1));
  if (start + 5 < courses.length)
    navigation.push(chatButton(state, "Ещё курсы", "page", page + 1));
  if (navigation.length) buttons.push(navigation);
  if (courses.length > 5 || state.courseSearch)
    buttons.push([chatButton(state, "Найти курс", "search")]);
  if (state.courseSearch)
    buttons.push([chatButton(state, "Все курсы", "menu")]);
  buttons.push([
    { type: "open_app", text: "Открыть приложение", web_app: botUsername },
  ]);
  return {
    text: courses.length
      ? `${state.courseSearch ? "Найдено курсов" : "Назначено курсов"}: ${courses.length}. Страница ${page + 1} из ${Math.ceil(courses.length / 5)}.\nВыберите курс. Здесь доступны AI, прогресс и короткие тесты.${courses.length > 5 ? "\nДля поиска напишите часть названия курса." : ""}\n\nКоды и личные данные в чат не отправляйте.`
      : state.courseSearch
        ? "Курс с таким названием не найден. Напишите другую часть названия или выберите «Все курсы»."
        : "Назначенных курсов пока нет. Уточните назначение у HR.",
    buttons,
  };
}

export function chatQuizQuestion(state: ChatState): ChatReply {
  const quiz = state.quiz!;
  const index = Object.keys(quiz.answers).length;
  const question = quiz.questions[index];
  const options = question.options.map((_, position) =>
    chatButton(state, String(position + 1), "answer", position),
  );
  return {
    text:
      `Вопрос ${index + 1} из ${quiz.questions.length}\n\n${question.prompt}\n\n` +
      question.options
        .map((option, position) => `${position + 1}. ${option}`)
        .join("\n"),
    buttons: [
      options.slice(0, 4),
      ...(options.length > 4 ? [options.slice(4)] : []),
      [chatButton(state, "Продолжить позже", "leave")],
    ],
  };
}
