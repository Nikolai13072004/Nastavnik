// Локальные данные для дизайн-предпросмотра /max?preview=1.
// Этот файл можно свободно менять: production-режим MAX его не использует.
export const DEMO_COURSES = [
  { id: "demo-onboarding", title: "Добро пожаловать в компанию", expiresAt: null },
  { id: "demo-security", title: "Информационная безопасность", expiresAt: "2026-12-31T18:00:00.000Z" },
];

export const DEMO_COURSE_DETAILS = {
  "demo-onboarding": {
    id: "demo-onboarding",
    title: "Добро пожаловать в компанию",
    description: "Короткий вводный курс: ценности, команда и первые шаги нового сотрудника.",
    completed: false,
    materials: [
      {
        id: "demo-material-1",
        title: "Как устроена работа",
        content: "<p>Мы собрали главное, что поможет уверенно начать работу.</p><ul><li>Где искать инструкции</li><li>К кому обращаться за помощью</li><li>Как проходить обучение</li></ul>",
        completed: true,
      },
      {
        id: "demo-material-2",
        title: "Ваш план на первую неделю",
        content: "<p>Познакомьтесь с командой, изучите рабочие инструменты и согласуйте цели с руководителем.</p>",
        completed: false,
      },
    ],
    quizzes: [{ id: "demo-quiz-1", title: "Проверка знаний", description: "Небольшой тест по вводному курсу.", questionCount: 3, maxAttempts: 3, attemptsUsed: 0, status: "NOT_STARTED", bestCorrectAnswers: 0 }],
    hasUnsupportedItems: false,
  },
  "demo-security": {
    id: "demo-security",
    title: "Информационная безопасность",
    description: "Практические правила безопасной работы с корпоративными данными.",
    completed: true,
    materials: [{ id: "demo-material-3", title: "Пароли и доступы", content: "<p>Используйте уникальные пароли и никогда не передавайте коды подтверждения другим людям.</p>", completed: true }],
    quizzes: [{ id: "demo-quiz-2", title: "Итоговый тест", description: null, questionCount: 5, maxAttempts: 2, attemptsUsed: 1, status: "PASSED", bestCorrectAnswers: 5 }],
    hasUnsupportedItems: false,
  },
} as const;
