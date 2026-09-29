"use client";

import Link from "next/link";
import Image from "next/image";
import Script from "next/script";
import { Button, MaxUI, Spinner, Typography } from "@maxhub/max-ui";
import "@maxhub/max-ui/dist/styles.css";
import { BookOpen, CheckCircle2, FileText, Link2, MessageCircle, RefreshCw } from "lucide-react";
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

const DESIGN_PREVIEW_STATE: LaunchState = {
  kind: "verified",
  firstName: "Алексей",
  employee: { name: "Алексей Смирнов", organizationName: "Демо-компания" },
  session: { token: "design-preview", expiresAt: "2099-01-01T00:00:00.000Z" },
  managerAccess: false,
};

export function MaxLaunch({ showLmsLinks = false, knowledgeCourseId, botUsername, designPreview = false }: {
  showLmsLinks?: boolean;
  knowledgeCourseId?: string;
  botUsername?: string;
  designPreview?: boolean;
}) {
  const [state, setState] = useState<LaunchState>(() => designPreview ? DESIGN_PREVIEW_STATE : { kind: "loading" });
  const [initialCourseId, setInitialCourseId] = useState<string | undefined>();
  const [mounted, setMounted] = useState(false);
  const pending = useRef<AbortController | null>(null);

  useEffect(() => {
    setMounted(true);
    if (designPreview) return;
    const timeout = window.setTimeout(() => {
      setState((current) => current.kind === "loading"
        ? { kind: "error", message: "MAX не ответил вовремя. Проверьте соединение и попробуйте снова." }
        : current);
    }, 15_000);
    return () => {
      window.clearTimeout(timeout);
      pending.current?.abort();
    };
  }, [designPreview]);

  const verify = useCallback(async () => {
    if (designPreview) {
      setState(DESIGN_PREVIEW_STATE);
      return;
    }
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
  }, [designPreview]);

  if (!mounted) {
    return <main className={styles.boot} aria-label="Загрузка Наставника">
      <Image
        className={styles.brandMark}
        src="/branding/nastavnik-avatar.png"
        alt=""
        width={36}
        height={36}
        unoptimized
      />
    </main>;
  }

  const status = state.kind === "loading"
    ? { icon: <Spinner size={24} />, eyebrow: "Подключение", title: "Проверяем вход…", text: "Подтверждаем безопасный запуск через MAX." }
    : state.kind === "outside"
      ? { icon: <MessageCircle size={24} aria-hidden />, eyebrow: "Нужен MAX", title: "Откройте приложение из MAX", text: "В чате с ботом нажмите «Открыть» внизу экрана. Обычная ссылка в браузере не передаёт данные для входа." }
      : state.kind === "verified"
        ? { icon: <CheckCircle2 size={24} aria-hidden />, eyebrow: "Готово", title: state.employee ? "Профиль подключён" : `${state.firstName}, подтвердите профиль`, text: state.employee
          ? `${state.employee.name} · ${state.employee.organizationName}`
          : showLmsLinks ? "Получите личный код в LMS и введите его ниже. Это нужно только при первом входе." : "HR или руководитель создаёт личный код в разделе HR и передаёт его вам. Вставьте код ниже при первом входе." }
        : { icon: <RefreshCw size={24} aria-hidden />, eyebrow: "Не удалось войти", title: "Подключение не завершено", text: state.message };

  return (
    <MaxUI resetBody={false} className={styles.maxUi}>
    <main className={styles.page}>
      {!designPreview && <Script src="https://st.max.ru/js/max-web-app.js" strategy="afterInteractive"
        onReady={() => void verify()}
        onError={() => setState({ kind: "error", message: "Не удалось подключиться к MAX. Проверьте соединение и откройте приложение повторно." })} />}
      <header className={styles.header}>
        <div className={styles.brandLockup}>
          <Image
            className={styles.brandMark}
            src="/branding/nastavnik-avatar.png"
            alt=""
            width={36}
            height={36}
            unoptimized
          />
          <span className={styles.brand}>Наставник <small>Обучение в MAX</small></span>
        </div>
        <span className={styles.preview}>{designPreview ? "Просмотр дизайна" : "MAX Mini App"}</span>
      </header>
      <section className={styles.content} aria-labelledby="launch-title">
        <div className={styles.hero}>
          <p className={styles.eyebrow}>Корпоративное обучение</p>
          <Typography.Headline asChild variant="large-strong"><h1 id="launch-title">Развивайтесь<br />каждый день</h1></Typography.Headline>
          <Typography.Body asChild variant="large"><p className={styles.intro}>Курсы, рабочие инструкции и проверка знаний — прямо в MAX.</p></Typography.Body>
          <div className={styles.heroVisual} aria-hidden>
            <span className={styles.heroBubble}><BookOpen size={28} /></span>
            <span className={styles.heroBubble}><FileText size={24} /></span>
            <span className={styles.heroBubble}><CheckCircle2 size={25} /></span>
          </div>
        </div>
        {designPreview ? <p className={styles.helper} role="status">
          Просмотр дизайна с вымышленными данными. Прогресс не сохраняется. Для обучения откройте бот в MAX.
        </p> : <div className={styles.status} role="status" aria-live="polite" aria-busy={state.kind === "loading"}>
          <span className={styles.statusIcon}>{status.icon}</span>
          <div><span className={styles.statusEyebrow}>{status.eyebrow}</span><h2>{status.title}</h2><p>{status.text}</p></div>
        </div>}
        {state.kind === "outside" && botUsername && /^[a-zA-Z0-9_]{3,64}$/.test(botUsername) &&
          <Button asChild stretched size="medium" iconBefore={<MessageCircle size={20} />}>
            <a href={`https://max.ru/${botUsername}`}>Открыть бот в MAX</a>
          </Button>}
        {state.kind === "verified" && !state.employee && <LinkEmployee onLinked={() => void verify()} />}
        {state.kind === "verified" && state.employee && state.session && <MaxCourses token={state.session.token}
          managerAccess={state.managerAccess} knowledgeCourseId={knowledgeCourseId} initialCourseId={initialCourseId}
          designPreview={designPreview} onRenew={() => void verify()} />}
        {state.kind === "verified" && state.employee && !state.session && <p>Не удалось открыть учебную сессию. Откройте приложение повторно.</p>}
        {state.kind === "error" && <Button stretched size="medium" iconBefore={<RefreshCw size={20} />} onClick={() => void verify()}>Повторить</Button>}
        {showLmsLinks && state.kind === "verified" && !state.employee ? <div className={styles.actions}>
          <p>Для получения кода войдите в свою учётную запись LMS. После входа откроется страница привязки. Если доступа нет, обратитесь к HR.</p>
          <Button asChild stretched size="medium" iconBefore={<Link2 size={20} />}><Link href="/connect-max">Войти в LMS и получить код</Link></Button>
          <Button asChild stretched size="medium" variant="secondary"><Link href="/login">Открыть веб-версию</Link></Button>
        </div> : null}

        <details className={styles.launchHelp}>
          <summary>{state.kind === "verified" && state.managerAccess ? "Как работать в Наставнике" : "Как начать обучение"}</summary>
          {state.kind === "verified" && state.managerAccess ? <ol>
            <li>В «Моём обучении» доступны ваши собственные курсы и тесты.</li>
            <li>В разделе HR добавляйте сотрудников, выдавайте коды и назначайте опубликованные курсы.</li>
            <li>Результаты и документы курса находятся в том же разделе HR.</li>
          </ol> : <>
            <ol>
              <li>Получите личный код у HR или руководителя. На демо-стенде — у участника с доступом HR.</li>
              <li>Откройте Mini App из чата с ботом, введите код и свяжите профиль.</li>
              <li>Откройте назначенный курс: изучайте материалы и проходите тесты.</li>
            </ol>
            <p>Код действует 15 минут. Если он истёк, запросите новый. Не отправляйте код в чат.</p>
          </>}
        </details>
        <details className={styles.launchHelp}>
          <summary>Как открыть меню в чате</summary>
          <p>Вернитесь в чат с ботом и отправьте <strong>Меню</strong> или <code>/start</code>. Появятся кнопки выбора курса, тестов и AI. Это можно делать в любой момент.</p>
          <p>Меню чата и мини-приложение показывают те же назначенные курсы и результаты.</p>
        </details>
      </section>
      <footer className={styles.footer}>Вопросы о доступе к обучению можно задать HR или руководителю.</footer>
    </main>
    </MaxUI>
  );
}
