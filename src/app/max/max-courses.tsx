"use client";

import { useEffect, useRef, useState } from "react";
import type { MaxCourse } from "@/modules/max/application/list-courses";
import styles from "./max.module.css";
import { MaxQuiz, type MaxQuizSummary } from "./max-quiz";
import { MaxCourseSearch } from "./max-course-search";
import { MaxCourseKnowledge } from "./max-course-knowledge";
import { MaxCourseDocuments } from "./max-course-documents";
import { MaxManagerReport } from "./max-manager-report";
import { DEMO_COURSES, DEMO_COURSE_DETAILS } from "./max-demo-data";
import { scrollToTarget } from "./scroll-to-target";

type CourseState =
  | { kind: "loading" }
  | { kind: "ready"; courses: MaxCourse[] }
  | { kind: "error"; message: string };

type CourseDetail = {
  id: string;
  title: string;
  description: string | null;
  completed: boolean;
  dueAt?: string | null;
  materials: Array<{ id: string; title: string; content: string | null; completed: boolean }>;
  quizzes: MaxQuizSummary[];
  hasUnsupportedItems: boolean;
};

function demoCourse(courseId: string): CourseDetail | null {
  const source = DEMO_COURSE_DETAILS[courseId as keyof typeof DEMO_COURSE_DETAILS];
  if (!source) return null;
  return {
    ...source,
    materials: source.materials.map((material) => ({ ...material })),
    quizzes: source.quizzes.map((quiz) => ({ ...quiz })),
  };
}

export function MaxCourses({ token, managerAccess, knowledgeCourseId, initialCourseId, designPreview = false, onRenew }: {
  token: string;
  managerAccess: boolean;
  knowledgeCourseId?: string;
  initialCourseId?: string;
  designPreview?: boolean;
  onRenew: () => void;
}) {
  const [state, setState] = useState<CourseState>({ kind: "loading" });
  const [detail, setDetail] = useState<CourseDetail | null>(null);
  const [detailMessage, setDetailMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [confirmMaterialId, setConfirmMaterialId] = useState<string | null>(null);
  const handledLaunch = useRef<string | undefined>(undefined);
  const courseHeading = useRef<HTMLHeadingElement | null>(null);
  const courseButtons = useRef(new Map<string, HTMLButtonElement>());
  const returnCourseId = useRef<string | null>(null);
  const openedCourseId = detail?.id;

  useEffect(() => {
    if (openedCourseId) scrollToTarget(courseHeading.current);
  }, [openedCourseId]);

  useEffect(() => {
    if (designPreview) {
      setState({ kind: "ready", courses: DEMO_COURSES.map((course) => ({ ...course })) });
      if (initialCourseId) setDetail(demoCourse(initialCourseId));
      return;
    }
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    async function load() {
      try {
        const response = await fetch("/api/max/courses", {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!active) return;
        if (!response.ok) {
          setState({ kind: "error", message: response.status === 401
            ? "Сессия истекла или доступ изменился. Повторите проверку входа."
            : "Курсы временно недоступны. Попробуйте ещё раз." });
          return;
        }
        const result = await response.json();
        if (!active) return;
        if (initialCourseId && handledLaunch.current !== initialCourseId) {
          if (!result.courses.some((course: MaxCourse) => course.id === initialCourseId)) {
            setDetailMessage("Курс из сообщения больше не доступен. Выберите курс из списка или обратитесь к HR.");
          } else {
            try {
              const courseResponse = await fetch(`/api/max/course?courseId=${encodeURIComponent(initialCourseId)}`, {
                headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
              });
              if (!courseResponse.ok) throw new Error();
              const loaded: { course: CourseDetail } = await courseResponse.json();
              if (active) setDetail(loaded.course);
            } catch {
              if (active) setDetailMessage("Курс из сообщения не открылся. Попробуйте открыть его из списка.");
            }
          }
          if (active) handledLaunch.current = initialCourseId;
        }
        if (active) {
          setState({ kind: "ready", courses: result.courses });
          if (refreshKey > 0) setRefreshMessage("Список курсов обновлён.");
        }
      } catch {
        if (active) setState({ kind: "error", message: "Не удалось загрузить курсы. Проверьте соединение." });
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void load();
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [token, refreshKey, initialCourseId, designPreview]);

  async function openCourse(courseId: string) {
    returnCourseId.current = courseId;
    if (designPreview) {
      setDetail(demoCourse(courseId));
      setConfirmMaterialId(null);
      setDetailMessage("");
      return;
    }
    setBusy(true);
    setDetailMessage("");
    try {
      const response = await fetch(`/api/max/course?courseId=${encodeURIComponent(courseId)}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!response.ok) throw new Error();
      const result: { course: CourseDetail } = await response.json();
      setDetail(result.course);
      setConfirmMaterialId(null);
    } catch {
      setDetailMessage("Не удалось открыть курс. Проверьте соединение и попробуйте снова.");
    } finally {
      setBusy(false);
    }
  }

  async function completeMaterial(materialId: string) {
    if (!detail || designPreview) return;
    setBusy(true);
    setDetailMessage("");
    try {
      const response = await fetch("/api/max/course", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ courseId: detail.id, materialId }),
      });
      if (!response.ok) throw new Error();
      setConfirmMaterialId(null);
      setDetail((current) => current && {
        ...current,
        materials: current.materials.map((material) => material.id === materialId
          ? { ...material, completed: true } : material),
      });
      void refreshCompletion(detail.id);
    } catch {
      setDetailMessage("Не удалось сохранить прогресс. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  }

  async function refreshCompletion(courseId: string) {
    if (designPreview) return;
    try {
      const response = await fetch(`/api/max/course?courseId=${encodeURIComponent(courseId)}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!response.ok) return;
      const result: { course: CourseDetail } = await response.json();
      setDetail((current) => current?.id === courseId
        ? { ...current, completed: result.course.completed } : current);
    } catch {
      // The saved quiz result stays visible; reopening the course refreshes completion.
    }
  }

  return (
    <section className={styles.courses} aria-labelledby="max-courses-title">
      <h2 id="max-courses-title">Моё обучение</h2>
      {state.kind === "loading" && <p role="status">Загружаем назначения…</p>}
      {state.kind === "error" && <p role="alert">{state.message}</p>}
      {detail ? <div className={styles.courseDetail}>
        <button type="button" className={styles.back} disabled={busy} onClick={() => {
          setDetail(null);
          setDetailMessage("");
          setConfirmMaterialId(null);
          window.requestAnimationFrame(() => {
            scrollToTarget(courseButtons.current.get(returnCourseId.current ?? "") ?? null);
          });
        }}>К моим курсам</button>
        <h3 ref={courseHeading} tabIndex={-1} className={styles.courseHeading}>{detail.title}</h3>
        {detail.dueAt && <p>Пройти до: {new Date(detail.dueAt).toLocaleString("ru-RU")}. Срок обучения не закрывает доступ к материалам.</p>}
        {detail.completed && <p className={styles.courseCompletion} role="status">
          {designPreview ? "Пример завершённого курса." : "Курс завершён. Результат сохранён."}
        </p>}
        {detail.description && <p>{detail.description}</p>}
        {detail.materials.map((material) => <article key={material.id} className={styles.material}>
          <h4>{material.title}</h4>
          {material.content && <div className={styles.materialContent} dangerouslySetInnerHTML={{ __html: material.content }} />}
          {designPreview ? <p className={styles.materialStatus}>
            {material.completed ? "Пример изученного материала." : "Отметки о прохождении доступны в MAX."}
          </p> : material.completed ? <p className={styles.materialStatus}>Изучено. Материал можно перечитать в любое время.</p>
            : confirmMaterialId === material.id ? <div className={styles.documentConfirm} aria-live="polite">
              <p>Вы прочитали материал? Подтверждение сохранит отметку о прохождении.</p>
              <button type="button" className={styles.retry} disabled={busy}
                onClick={() => void completeMaterial(material.id)}>{busy ? "Сохраняем..." : "Да, материал изучен"}</button>
              <button type="button" className={styles.back} disabled={busy}
                onClick={() => setConfirmMaterialId(null)}>Продолжить чтение</button>
            </div> : <button type="button" className={styles.retry} disabled={busy}
              onClick={() => setConfirmMaterialId(material.id)}>Отметить как изученное</button>}
        </article>)}
        {designPreview ? <>
          <section className={styles.courseSearch} aria-label="Демо рабочих инструкций">
            <h4>Рабочие инструкции</h4>
            <p>Документы компании и поиск по базе знаний доступны в MAX. Здесь показан только дизайн.</p>
            <button type="button" className={styles.retry} disabled>Открыть инструкции</button>
          </section>
          {detail.quizzes.map((quiz) => <section key={quiz.id} className={styles.quiz} aria-label={quiz.title}>
            <h4>{quiz.title}</h4>
            {quiz.description && <p>{quiz.description}</p>}
            <p>{quiz.status === "PASSED" ? `Сдано: ${quiz.bestCorrectAnswers} из ${quiz.questionCount} верно.`
              : `Вопросов: ${quiz.questionCount}. Попыток осталось: ${quiz.maxAttempts - quiz.attemptsUsed}.`}</p>
            {quiz.status !== "PASSED" && <>
              <p>Для прохождения теста откройте приложение из MAX.</p>
              <button type="button" className={styles.retry} disabled>Начать тест</button>
            </>}
          </section>)}
        </> : <>
        <MaxCourseDocuments key={`documents:${detail.id}`} courseId={detail.id} token={token}
          onRenew={onRenew} />
        {knowledgeCourseId === detail.id
          ? <MaxCourseKnowledge key={`knowledge:${detail.id}`} courseId={detail.id} token={token}
              onRenew={onRenew} />
          : <MaxCourseSearch key={`search:${detail.id}`} courseId={detail.id} token={token} onRenew={onRenew} />}
        {detail.quizzes.map((quiz) => <MaxQuiz key={quiz.id} token={token} courseId={detail.id} quiz={quiz}
          onResult={(result) => {
            setDetail((current) => current && {
              ...current,
              quizzes: current.quizzes.map((item) => item.id === quiz.id ? {
                ...item,
                status: item.status === "PASSED" ? "PASSED" : result.outcome,
                attemptsUsed: item.attemptsUsed + 1,
                bestCorrectAnswers: Math.max(item.bestCorrectAnswers, result.correctAnswers),
              } : item),
            });
            if (result.outcome === "PASSED") void refreshCompletion(detail.id);
          }} />)}
        </>}
        {detail.hasUnsupportedItems && <p>Некоторые форматы материалов или тестов пока не доступны в мини-приложении.</p>}
      </div> : state.kind === "ready" && (state.courses.length === 0
        ? <p>Сейчас нет доступных курсов. Новые назначения появятся здесь после публикации и назначения в LMS.</p>
        : <ul>{[...state.courses].sort((left, right) =>
          Number(right.id === knowledgeCourseId) - Number(left.id === knowledgeCourseId))
          .map((course) => <li key={course.id}>
          <h3>{course.title}</h3>
          <p>{course.expiresAt ? `Доступ до ${new Date(course.expiresAt).toLocaleString("ru-RU")}` : "Без ограничения срока"}</p>
          <button type="button" className={styles.retry} disabled={busy}
            ref={(element) => {
              if (element) courseButtons.current.set(course.id, element);
              else courseButtons.current.delete(course.id);
            }}
            onClick={() => void openCourse(course.id)}>Открыть курс</button>
        </li>)}</ul>)}
      {detailMessage && <p role="alert">{detailMessage}</p>}
      {!detail && <>
        <button type="button" className={styles.back} disabled={state.kind === "loading" || busy} onClick={() => {
          if (designPreview) {
            setRefreshMessage("Показаны вымышленные данные для просмотра дизайна.");
            return;
          }
          if (state.kind === "error") { onRenew(); return; }
          setRefreshMessage("");
          setState({ kind: "loading" });
          setRefreshKey((current) => current + 1);
        }}>{state.kind === "loading" ? "Загружаем курсы..." : state.kind === "error" ? "Повторить вход" : "Проверить новые курсы"}</button>
        {refreshMessage && <p role="status">{refreshMessage}</p>}
      </>}
      {managerAccess && !designPreview && <MaxManagerReport token={token} onRenew={onRenew} />}
    </section>
  );
}
