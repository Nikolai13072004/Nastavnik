import type { AssessmentQuestion } from "@/modules/assessment/domain/assessment";
import type { PublishedCourseSnapshotQuiz } from "@/lib/course-content";

export type MaxQuizQuestion = { id: string; prompt: string; options: string[] };

function readOptions(question: AssessmentQuestion): string[] | null {
  if (question.type !== "SINGLE_CHOICE") return null;
  try {
    const config: unknown = JSON.parse(question.config);
    if (!config || typeof config !== "object" || !("options" in config) || !Array.isArray(config.options) ||
        !("correctIndex" in config) || !Number.isInteger(config.correctIndex)) return null;
    const options = config.options;
    const correctIndex = config.correctIndex as number;
    if (options.length < 2 || options.length > 8 ||
        !options.every((option) => typeof option === "string" && option.trim().length > 0 && option.length <= 500) ||
        correctIndex < 0 || correctIndex >= options.length) return null;
    return options;
  } catch {
    return null;
  }
}

export function isSupportedMaxQuiz(quiz: PublishedCourseSnapshotQuiz, resultViewMode: string) {
  return resultViewMode === "SCORE_ONLY" && quiz.maxAttempts >= 1 &&
    quiz.questionPoolSize === null && !quiz.timeLimitMinutes &&
    !quiz.lockMaterialsOnStart && !quiz.trackSecurityEvents &&
    quiz.questions.length > 0 && quiz.questions.length <= 20 &&
    quiz.questions.every((question) => readOptions(question) !== null);
}

export function toMaxQuizQuestions(questions: AssessmentQuestion[]): MaxQuizQuestion[] | null {
  const projected = questions.map((question) => {
    const options = readOptions(question);
    return options ? { id: question.id, prompt: question.prompt, options } : null;
  });
  return projected.every((question) => question !== null) ? projected as MaxQuizQuestion[] : null;
}

export function validateMaxQuizAnswers(questions: AssessmentQuestion[], raw: unknown) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const entries = Object.entries(raw);
  if (entries.length !== questions.length) return null;
  const answers: Record<string, number> = {};
  for (const question of questions) {
    const options = readOptions(question);
    const answer = (raw as Record<string, unknown>)[question.id];
    if (!options || !Number.isInteger(answer) || (answer as number) < 0 || (answer as number) >= options.length) {
      return null;
    }
    answers[question.id] = answer as number;
  }
  return answers;
}
