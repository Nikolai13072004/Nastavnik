export type AssessmentQuestion = {
  id: string;
  orderIndex: number;
  type: string;
  prompt: string;
  config: string;
  points: number;
};

type ScorableAssessmentQuestion = Pick<AssessmentQuestion, "id" | "type" | "config" | "points">;

export type AssessmentOutcome = "IN_PROGRESS" | "PENDING_REVIEW" | "PASSED" | "FAILED";

export type AssessmentAttempt = {
  id: string;
  attemptNumber: number;
  outcome: string;
  score: number;
  maxScore: number;
  correctAnswers: number;
  totalQuestions: number;
  answers: string;
  questionSnapshot: string;
  createdAt: Date;
  completedAt: Date;
};

type SingleChoiceConfig = { correctIndex?: number };
type OpenConfig = { sampleAnswer?: string; reviewMode?: "AUTO" | "MANUAL" };
type MatchConfig = { correctPairs?: number[] };

export class AssessmentDomainError extends Error {
  constructor(
    readonly code:
      | "ALREADY_PASSED"
      | "PENDING_REVIEW"
      | "ATTEMPTS_EXHAUSTED"
      | "RETRY_DELAY"
      | "TIME_LIMIT_EXPIRED"
      | "ATTEMPT_NOT_ACTIVE",
    message: string,
  ) {
    super(message);
    this.name = "AssessmentDomainError";
  }
}

function parseConfig<T>(raw: string): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return {} as T;
  }
}

function normalize(value: string) {
  return value.trim().toLowerCase();
}

export function getRequiredCorrectAnswers(minCorrectAnswers: number, totalQuestions: number) {
  if (totalQuestions <= 0) return 0;
  const normalized = Number.isFinite(minCorrectAnswers) ? Math.floor(minCorrectAnswers) : 1;
  return Math.max(1, Math.min(normalized, totalQuestions));
}

export function scoreAssessmentQuestion(question: ScorableAssessmentQuestion, rawAnswer: unknown) {
  const max = Math.max(0, Math.floor(question.points));
  let isCorrect = false;
  let requiresManualReview = false;

  if (question.type === "SINGLE_CHOICE") {
    const config = parseConfig<SingleChoiceConfig>(question.config);
    const answer = typeof rawAnswer === "number" ? rawAnswer : Number.parseInt(String(rawAnswer ?? ""), 10);
    isCorrect = Number.isInteger(answer) && answer === config.correctIndex;
  } else if (question.type === "OPEN") {
    const config = parseConfig<OpenConfig>(question.config);
    requiresManualReview = config.reviewMode === "MANUAL";
    if (!requiresManualReview) {
      const answer = typeof rawAnswer === "string" ? normalize(rawAnswer) : "";
      const expected = config.sampleAnswer ? normalize(config.sampleAnswer) : "";
      isCorrect = Boolean(answer && expected && answer === expected);
    }
  } else if (question.type === "MATCHING") {
    const config = parseConfig<MatchConfig>(question.config);
    const expected = config.correctPairs ?? [];
    const answer = Array.isArray(rawAnswer)
      ? rawAnswer.map((value) => Number.parseInt(String(value), 10))
      : [];
    isCorrect = answer.length === expected.length && expected.every((value, index) => answer[index] === value);
  } else if (question.type === "FILE") {
    requiresManualReview = true;
  }

  return {
    questionId: question.id,
    earned: isCorrect ? max : 0,
    max,
    isCorrect,
    requiresManualReview,
  };
}

export function scoreAssessment(questions: AssessmentQuestion[], answers: Record<string, unknown>) {
  const results = questions
    .slice()
    .sort((left, right) => left.orderIndex - right.orderIndex)
    .map((question) => scoreAssessmentQuestion(question, answers[question.id]));

  return {
    score: results.reduce((sum, result) => sum + result.earned, 0),
    maxScore: results.reduce((sum, result) => sum + result.max, 0),
    correctAnswers: results.filter((result) => result.isCorrect).length,
    totalQuestions: questions.length,
    hasPendingReview: results.some((result) => result.requiresManualReview),
    results,
  };
}

export function getBestAssessmentAttempt<T extends Pick<AssessmentAttempt, "correctAnswers" | "score" | "attemptNumber">>(attempts: T[]) {
  return attempts.slice().sort((left, right) =>
    right.correctAnswers - left.correctAnswers ||
    right.score - left.score ||
    right.attemptNumber - left.attemptNumber
  )[0] ?? null;
}

export function getAssessmentStatus(args: {
  attempts: Array<Pick<AssessmentAttempt, "outcome">>;
  bestOutcome: string | null;
  maxAttempts: number;
}) {
  const completedAttempts = args.attempts.filter((attempt) => attempt.outcome !== "IN_PROGRESS");
  if (args.bestOutcome === "PASSED") return "PASSED";
  if (args.attempts.some((attempt) => attempt.outcome === "IN_PROGRESS")) return "IN_PROGRESS";
  if (args.attempts.some((attempt) => attempt.outcome === "PENDING_REVIEW")) return "PENDING_REVIEW";
  return completedAttempts.length >= Math.max(args.maxAttempts, 1) ? "FAILED" : "IN_PROGRESS";
}

export function assertAttemptAvailable(args: {
  completedAttempts: AssessmentAttempt[];
  maxAttempts: number;
  retryDelayMinutes: number | null;
  now: Date;
  practice?: boolean;
}) {
  if (!args.practice && args.completedAttempts.some((attempt) => attempt.outcome === "PASSED")) {
    throw new AssessmentDomainError("ALREADY_PASSED", "Тест уже успешно сдан.");
  }
  if (args.completedAttempts.some((attempt) => attempt.outcome === "PENDING_REVIEW")) {
    throw new AssessmentDomainError("PENDING_REVIEW", "Тест уже отправлен на проверку.");
  }
  if (!args.practice && args.completedAttempts.length >= Math.max(args.maxAttempts, 1)) {
    throw new AssessmentDomainError("ATTEMPTS_EXHAUSTED", "Попытки закончились.");
  }

  if (!args.practice && args.retryDelayMinutes && args.retryDelayMinutes > 0) {
    const latestFailure = args.completedAttempts
      .filter((attempt) => attempt.outcome === "FAILED")
      .sort((left, right) => right.completedAt.getTime() - left.completedAt.getTime())[0];
    const availableAt = latestFailure
      ? new Date(latestFailure.completedAt.getTime() + args.retryDelayMinutes * 60_000)
      : null;
    if (availableAt && availableAt > args.now) {
      throw new AssessmentDomainError(
        "RETRY_DELAY",
        `Следующая попытка будет доступна ${availableAt.toLocaleString("ru-RU")}.`,
      );
    }
  }
}

// Не-бросающий предикат истечения времени попытки. Единый источник правила:
// бросающий assertTimeLimit использует его же, «тихие» пути (save-draft) — напрямую.
export function isAssessmentTimeLimitExpired(args: {
  attempt: Pick<AssessmentAttempt, "createdAt"> | null;
  timeLimitMinutes: number | null;
  now: Date;
  submissionGraceMs?: number;
}): boolean {
  if (!args.attempt || !args.timeLimitMinutes) return false;
  const expiresAt = args.attempt.createdAt.getTime() + args.timeLimitMinutes * 60_000;
  return args.now.getTime() > expiresAt + Math.max(0, args.submissionGraceMs ?? 0);
}

export function assertTimeLimit(args: {
  attempt: Pick<AssessmentAttempt, "createdAt"> | null;
  timeLimitMinutes: number | null;
  now: Date;
  submissionGraceMs?: number;
}) {
  if (isAssessmentTimeLimitExpired(args)) {
    throw new AssessmentDomainError("TIME_LIMIT_EXPIRED", "Время прохождения теста истекло.");
  }
}

export function serializeQuestionSnapshot(questions: AssessmentQuestion[]) {
  return JSON.stringify(questions.map(({ id, orderIndex, type, prompt, config, points }) => ({
    id,
    orderIndex,
    type,
    prompt,
    config,
    points,
  })));
}
