import type { AssessmentQuestion } from "@/modules/assessment/domain/assessment";

export function describeAttemptQuestions(
  questionSnapshot: string,
  rawAnswers: string,
  outcome: string,
) {
  try {
    const questions: AssessmentQuestion[] = JSON.parse(questionSnapshot);
    const answers: Record<string, unknown> = JSON.parse(rawAnswers);
    if (!Array.isArray(questions) || !answers || typeof answers !== "object")
      return [];
    return questions.map((question) => {
      const config = JSON.parse(question.config);
      const options: string[] = Array.isArray(config.options)
        ? config.options
        : [];
      const selected = answers[question.id];
      const supported =
        question.type === "SINGLE_CHOICE" &&
        Number.isInteger(config.correctIndex);
      const submitted = outcome === "PASSED" || outcome === "FAILED";
      return {
        id: question.id,
        prompt: question.prompt,
        options,
        selected:
          typeof selected === "number"
            ? (options[selected] ?? "Не отвечено")
            : "Нет ответа с выбором варианта",
        correct: supported ? (options[config.correctIndex] ?? null) : null,
        isCorrect:
          supported && submitted ? selected === config.correctIndex : null,
        analyticsKey: JSON.stringify([
          question.id,
          question.prompt,
          [...options].sort(),
          supported ? options[config.correctIndex] : null,
        ]),
      };
    });
  } catch {
    return [];
  }
}

export function aggregateQuestionErrors(
  attempts: Array<{
    questionSnapshot: string;
    answers: string;
    outcome: string;
  }>,
) {
  const rows = new Map<
    string,
    {
      prompt: string;
      correct: string | null;
      answered: number;
      incorrect: number;
    }
  >();
  for (const attempt of attempts) {
    for (const question of describeAttemptQuestions(
      attempt.questionSnapshot,
      attempt.answers,
      attempt.outcome,
    )) {
      if (question.isCorrect === null) continue;
      const row = rows.get(question.analyticsKey) ?? {
        prompt: question.prompt,
        correct: question.correct,
        answered: 0,
        incorrect: 0,
      };
      row.answered++;
      if (!question.isCorrect) row.incorrect++;
      rows.set(question.analyticsKey, row);
    }
  }
  return [...rows.values()].sort(
    (a, b) => b.incorrect - a.incorrect || b.answered - a.answered,
  );
}

export function reportCsv(rows: Array<Array<unknown>>) {
  return (
    "\uFEFF" +
    rows
      .map((row) =>
        row
          .map((value) => {
            let text =
              value === null || value === undefined ? "" : String(value);
            if (/^[\s\u0000-\u001f]*[=+@-]/u.test(text)) text = "'" + text;
            return `"${text.replace(/"/g, '""')}"`;
          })
          .join(";"),
      )
      .join("\r\n")
  );
}

export function studyStatus(
  completed: boolean,
  pendingDocuments: number,
  dueAt: Date | null,
  now = new Date(),
) {
  if (completed && pendingDocuments === 0) return "COMPLETED";
  if (dueAt && dueAt <= now) return "OVERDUE";
  return pendingDocuments > 0 ? "UPDATED_DOCUMENTS" : "IN_PROGRESS";
}

export function shouldRemind(dueAt: Date | null, now: Date) {
  return Boolean(dueAt && dueAt.getTime() <= now.getTime() + 3 * 86_400_000);
}
