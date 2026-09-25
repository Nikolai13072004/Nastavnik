"use client";

import { useState } from "react";
import styles from "./max.module.css";

export type MaxQuizSummary = {
  id: string;
  title: string;
  description: string | null;
  questionCount: number;
  maxAttempts: number;
  attemptsUsed: number;
  status: string;
  bestCorrectAnswers: number;
};

type Question = { id: string; prompt: string; options: string[] };
type Result = { outcome: string; correctAnswers: number; totalQuestions: number };
type QuizState =
  | { kind: "idle" }
  | { kind: "answering"; attemptId: string; questions: Question[]; answers: Record<string, number> }
  | { kind: "result"; result: Result };

const errorMessages: Record<string, string> = {
  MATERIAL_REQUIRED: "Сначала отметьте предыдущий материал как изученный.",
  ATTEMPTS_EXHAUSTED: "Попытки закончились.",
  ALREADY_PASSED: "Тест уже сдан.",
  PENDING_REVIEW: "Тест ожидает проверки.",
  RETRY_DELAY: "Повторная попытка пока недоступна.",
  SESSION_EXPIRED: "Сессия MAX истекла. Откройте мини-приложение заново.",
  ATTEMPT_NOT_ACTIVE: "Попытка уже завершена. Обновите курс.",
};

export function MaxQuiz({ token, courseId, quiz, onResult }: {
  token: string;
  courseId: string;
  quiz: MaxQuizSummary;
  onResult: (result: Result) => void;
}) {
  const [state, setState] = useState<QuizState>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(body: object) {
    const response = await fetch("/api/max/quiz", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ courseId, quizId: quiz.id, ...body }),
      cache: "no-store",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(errorMessages[data.error] ?? "Не удалось выполнить действие. Попробуйте ещё раз.");
    return data;
  }

  async function start() {
    setBusy(true);
    setError("");
    try {
      const data: { attemptId: string; questions: Question[] } = await send({ action: "start" });
      setState({ kind: "answering", attemptId: data.attemptId, questions: data.questions, answers: {} });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось начать тест.");
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (state.kind !== "answering") return;
    if (state.questions.some((question) => !(question.id in state.answers))) {
      setError("Ответьте на все вопросы.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const data: { result: Result } = await send({
        action: "submit", attemptId: state.attemptId, answers: state.answers,
      });
      setState({ kind: "result", result: data.result });
      onResult(data.result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось отправить ответы.");
    } finally {
      setBusy(false);
    }
  }

  const passed = quiz.status === "PASSED";
  const exhausted = quiz.attemptsUsed >= quiz.maxAttempts;

  return <section className={styles.quiz} aria-label={quiz.title}>
    <h4>{quiz.title}</h4>
    {quiz.description && <p>{quiz.description}</p>}
    {state.kind === "idle" && <>
      <p>{passed ? `Сдано: ${quiz.bestCorrectAnswers} из ${quiz.questionCount} верно.`
        : exhausted ? "Попытки закончились."
        : `Вопросов: ${quiz.questionCount} · попыток осталось: ${quiz.maxAttempts - quiz.attemptsUsed}`}</p>
      {!passed && !exhausted && <button type="button" className={styles.retry} disabled={busy}
        onClick={() => void start()}>{busy ? "Открываем…" : "Начать тест"}</button>}
    </>}
    {state.kind === "answering" && <>
      {state.questions.map((question, index) => <fieldset key={question.id} className={styles.quizQuestion}>
        <legend>{index + 1}. {question.prompt}</legend>
        {question.options.map((option, optionIndex) => <label key={optionIndex}>
          <input type="radio" name={question.id} value={optionIndex}
            checked={state.answers[question.id] === optionIndex}
            disabled={busy}
            onChange={() => setState((current) => current.kind === "answering"
              ? { ...current, answers: { ...current.answers, [question.id]: optionIndex } } : current)} />
          <span>{option}</span>
        </label>)}
      </fieldset>)}
      <button type="button" className={styles.retry} disabled={busy} onClick={() => void submit()}>
        {busy ? "Сохраняем…" : "Отправить ответы"}
      </button>
    </>}
    {state.kind === "result" && <>
      <p role="status">{state.result.outcome === "PASSED" ? "Тест сдан" : "Тест не сдан"} · верно {state.result.correctAnswers} из {state.result.totalQuestions}.</p>
      {state.result.outcome === "FAILED" && quiz.attemptsUsed < quiz.maxAttempts &&
        <button type="button" className={styles.retry} disabled={busy} onClick={() => void start()}>Повторить тест</button>}
    </>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
