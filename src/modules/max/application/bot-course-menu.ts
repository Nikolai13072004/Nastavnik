import {
  createListMaxCourses,
  type MaxCourse,
  type MaxLearnerRepository,
} from "./list-courses";

export const courseLaunchPrefix = "course_";

export function parseCourseLaunch(
  value: string | null | undefined,
): string | undefined {
  if (!value?.startsWith(courseLaunchPrefix)) return undefined;
  const id = value.slice(courseLaunchPrefix.length);
  return /^[a-zA-Z0-9_-]{1,128}$/.test(id) ? id : undefined;
}

export function createLoadBotCourses(repository: MaxLearnerRepository) {
  const list = createListMaxCourses(repository);
  return async (maxUserId: string): Promise<MaxCourse[] | null> => {
    const identity = await repository.findIdentity(maxUserId);
    return identity ? list(identity) : null;
  };
}

export function buildBotCourseMenu(
  courses: MaxCourse[],
  botUsername: string,
  preferredCourseId?: string,
) {
  const available = courses.filter((course) =>
    parseCourseLaunch(`${courseLaunchPrefix}${course.id}`),
  );
  const sorted = available
    .filter((course) => course.id === preferredCourseId)
    .concat(available.filter((course) => course.id !== preferredCourseId));
  const shown = sorted.slice(0, 10);
  const titles = shown.map(
    (course, index) =>
      `${index + 1}. ${course.title.replace(/[\r\n\t]/g, " ").slice(0, 180)}`,
  );
  const text =
    shown.length > 0
      ? `Ваши назначенные курсы:\n${titles.join("\n")}\n\nВыберите номер курса по кнопке ниже. Материалы, AI и тесты откроются в приложении.`
      : "Сейчас нет доступных назначенных курсов. Если вы ожидаете обучение, обратитесь к HR или руководителю.";
  const buttons = shown.map((course, index) => [
    {
      type: "open_app" as const,
      text: `Курс ${index + 1}`,
      web_app: botUsername,
      payload: `${courseLaunchPrefix}${course.id}`,
    },
  ]);
  return {
    text: `${text}${available.length > shown.length ? "\nОстальные курсы доступны в списке приложения." : ""}\nНе отправляйте в чат коды привязки или личные данные.`,
    buttons: [
      ...buttons,
      [{ type: "open_app" as const, text: "Все курсы", web_app: botUsername }],
    ],
  };
}
