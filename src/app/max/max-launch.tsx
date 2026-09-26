"use client";

import Link from "next/link";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./max.module.css";
import { LinkEmployee } from "./link-employee";
import { MaxCourses } from "./max-courses";
import { parseCourseLaunch } from "@/modules/max/application/bot-course-menu";

type Employee = { name: string; organizationName: string };

type LaunchState =
  | { kind: "loading" }
  | { kind: "outside" }
  | { kind: "verified"; firstName: string; employee: Employee | null; session: { token: string; expiresAt: string } | null; managerAccess: boolean }
  | { kind: "error"; message: string };

export function MaxLaunch({ showLmsLinks = true, knowledgeCourseId }: {
  showLmsLinks?: boolean;
  knowledgeCourseId?: string;
}) {
  const [state, setState] = useState<LaunchState>({ kind: "loading" });
  const [initialCourseId, setInitialCourseId] = useState<string | undefined>();
  const pending = useRef<AbortController | null>(null);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setState((current) => current.kind === "loading"
        ? { kind: "error", message: "MAX не ответил вовремя. Проверьте соединение и попробуйте снова." }
        : current);
    }, 15_000);
    return () => {
      window.clearTimeout(timeout);
      pending.current?.abort();
    };
  }, []);

  const verify = useCallback(async () => {
    pending.current?.abort();
    const bridge = (window as Window & { WebApp?: { initData?: string } }).WebApp;
    if (!bridge) {
      setState({ kind: "error", message: "Не удалось загрузить соединение с MAX. Откройте приложение повторно." });
      return;
    }
    if (!bridge.initData) {
      setState({ kind: "outside" });
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setState({ kind: "loading" });
    try {
      const response = await fetch("/api/max/identity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initData: bridge.initData }),
        signal: controller.signal,
        cache: "no-store",
      });
      if (pending.current !== controller) return;
      const data = await response.json();
      if (!response.ok) {
        setState({ kind: "error", message: data.error === "MAX_NOT_CONFIGURED"
          ? "Обучение пока недоступно. Сообщите HR или руководителю."
          : response.status === 503 ? "Сервис временно недоступен. Попробуйте позже."
          : "Не удалось подтвердить вход. Закройте мини-приложение и снова откройте его из бота." });
        return;
      }
      if (data.status !== "identity_verified" || typeof data.firstName !== "string") throw new Error("Invalid response");
      // This is only navigation. The courses API still authorizes every read.
      setInitialCourseId(parseCourseLaunch(new URLSearchParams(bridge.initData).get("start_param")));
      setState({ kind: "verified", firstName: data.firstName, employee: data.employee ?? null,
        session: data.session ?? null, managerAccess: data.managerAccess === true });
    } catch {
      if (pending.current === controller) {
        setState({ kind: "error", message: "Сервер недоступен. Проверьте соединение и повторите попытку." });
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }, []);

  return (
    <main className={styles.page}>
      <Script src="https://st.max.ru/js/max-web-app.js" strategy="afterInteractive"
        onReady={() => void verify()}
        onError={() => setState({ kind: "error", message: "Не удалось подключиться к MAX. Проверьте соединение и откройте приложение повторно." })} />
      <header className={styles.header}>
        <span className={styles.brand}>Prodigy <span>/ MAX</span></span>
        <span className={styles.preview}>Подключение</span>
      </header>
      <section className={styles.content} aria-labelledby="launch-title">
        <p className={styles.eyebrow}>Обучение сотрудников</p>
        <h1 id="launch-title">Рабочие знания.<br />В привычном чате.</h1>
        <p className={styles.intro}>Курсы и учебные материалы вашей компании прямо в MAX.</p>
        <div className={styles.status} role="status" aria-live="polite" aria-busy={state.kind === "loading"}>
          {state.kind === "loading" && <><h2>Проверяем вход…</h2><p>Подтверждаем данные запуска через MAX.</p></>}
          {state.kind === "outside" && <><h2>Откройте приложение из MAX</h2><p>В чате с ботом компании нажмите кнопку мини-приложения. Обычная ссылка в браузере не передаёт данные для входа.</p></>}
          {state.kind === "verified" && <>
            <h2>{state.employee ? "Учётная запись связана" : state.firstName ? `${state.firstName}, профиль MAX подтверждён` : "Профиль MAX подтверждён"}</h2>
            {state.employee ? <p>{state.employee.name} · {state.employee.organizationName}</p>
              : <p>{showLmsLinks
                ? "Для привязки получите код в своей учётной записи LMS и введите его ниже."
                : "Получите одноразовый код у HR или руководителя и введите его ниже."}</p>}
          </>}
          {state.kind === "error" && <><h2>Подключение не завершено</h2><p>{state.message}</p></>}
        </div>
        {state.kind === "verified" && !state.employee && <LinkEmployee onLinked={() => void verify()} />}
        {state.kind === "verified" && state.employee && state.session && <MaxCourses token={state.session.token}
          managerAccess={state.managerAccess} knowledgeCourseId={knowledgeCourseId} initialCourseId={initialCourseId}
          onRenew={() => void verify()} />}
        {state.kind === "verified" && state.employee && !state.session && <p>Не удалось открыть учебную сессию. Откройте приложение повторно.</p>}
        {state.kind === "error" && <button className={styles.retry} onClick={() => void verify()}>Повторить проверку</button>}
        {showLmsLinks ? <>
          <Link className={styles.login} href="/connect-max">Получить код в LMS</Link>
          <br />
          <Link className={styles.login} href="/login">Войти в веб-версию LMS</Link>
        </> : null}
      </section>
      <footer className={styles.footer}>Вопросы о доступе к обучению можно задать HR или руководителю.</footer>
    </main>
  );
}
