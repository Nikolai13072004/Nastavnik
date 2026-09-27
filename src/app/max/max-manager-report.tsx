"use client";

import { type FormEvent, useEffect, useState } from "react";
import { MaxManagerDocuments } from "./max-manager-documents";
import styles from "./max.module.css";

type Course = { id: string; title: string };
type Assignment = {
  canAssign: boolean;
  canCreate: boolean;
  learners: Array<{ id: string; name: string; canIssueCode: boolean }>;
  directoryTruncated: boolean;
};
type Report = {
  title: string;
  quizCount: number;
  assignedCount: number;
  completedCount: number;
  truncated: boolean;
  learners: Array<{ id: string; name: string; completed: boolean; passedQuizzes: number }>;
};

export function MaxManagerReport({ token, onRenew }: { token: string; onRenew: () => void }) {
  const [open, setOpen] = useState(false);
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [learnerId, setLearnerId] = useState("");
  const [assignmentMessage, setAssignmentMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [employeeName, setEmployeeName] = useState("");
  const [employeeLastName, setEmployeeLastName] = useState("");
  const [employeeEmail, setEmployeeEmail] = useState("");
  const [employeeCode, setEmployeeCode] = useState<{ token: string; expiresAt: string } | null>(null);
  const [employeeMessage, setEmployeeMessage] = useState("");
  const [creatingEmployee, setCreatingEmployee] = useState(false);
  const [codeUserId, setCodeUserId] = useState("");
  const [issuingCode, setIssuingCode] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let active = true;
    async function load() {
      setBusy(true);
      setMessage("");
      try {
        const query = courseId ? `?courseId=${encodeURIComponent(courseId)}` : "";
        const response = await fetch(`/api/max/manager-report${query}`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          if (active) setMessage(response.status === 401 || response.status === 403
            ? "Доступ к отчёту изменился. Проверьте вход снова."
            : "Не удалось загрузить отчёт. Попробуйте снова.");
          return;
        }
        const data: { courses: Course[]; report: Report | null; assignment: Assignment } = await response.json();
        if (active) {
          setCourses(data.courses);
          setReport(data.report);
          setAssignment(data.assignment);
        }
      } catch {
        if (active) setMessage("Не удалось загрузить отчёт. Проверьте соединение.");
      } finally {
        if (active) setBusy(false);
      }
    }
    void load();
    return () => { active = false; controller.abort(); };
  }, [open, courseId, token, refreshKey]);

  async function assignCourse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!courseId || !learnerId || saving) return;
    setSaving(true);
    setAssignmentMessage("");
    try {
      const response = await fetch("/api/max/manager-report", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ courseId, learnerId }),
        cache: "no-store",
      });
      if (!response.ok) {
        setAssignmentMessage(response.status === 401 || response.status === 403
          ? "Доступ изменился. Проверьте вход снова."
          : response.status === 404 ? "Курс или сотрудник больше не доступны. Обновите отчёт."
          : "Не удалось назначить курс. Попробуйте снова.");
        return;
      }
      const result: { status: "ASSIGNED" | "RENEWED" | "ALREADY_ASSIGNED" } = await response.json();
      setAssignmentMessage(result.status === "ASSIGNED" ? "Курс назначен. Он появится у сотрудника после обновления Mini App."
        : result.status === "RENEWED" ? "Доступ к курсу возобновлён."
        : "Курс уже назначен этому сотруднику.");
      if (result.status !== "ALREADY_ASSIGNED") setRefreshKey((value) => value + 1);
    } catch {
      setAssignmentMessage("Не удалось связаться с сервером. Проверьте соединение и попробуйте снова.");
    } finally {
      setSaving(false);
    }
  }

  async function addEmployee(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creatingEmployee) return;
    setCreatingEmployee(true);
    setEmployeeCode(null);
    setEmployeeMessage("");
    try {
      const response = await fetch("/api/max/employees", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ firstName: employeeName, lastName: employeeLastName, email: employeeEmail }),
        cache: "no-store",
      });
      if (!response.ok) {
        setEmployeeMessage(response.status === 409 ? "Не удалось использовать этот email. Проверьте адрес или обратитесь к администратору."
          : response.status === 400 ? "Проверьте имя и рабочий email."
          : response.status === 401 || response.status === 403 ? "Доступ изменился. Проверьте вход снова."
          : "Не удалось добавить сотрудника. Проверьте соединение и попробуйте снова.");
        return;
      }
      const result: { status: "CREATED"; token: string; expiresAt: string } = await response.json();
      setEmployeeCode({ token: result.token, expiresAt: result.expiresAt });
      setEmployeeName("");
      setEmployeeLastName("");
      setEmployeeEmail("");
      setRefreshKey((value) => value + 1);
    } catch {
      setEmployeeMessage("Нет ответа от сервера. Перед повтором проверьте список сотрудников: запись могла сохраниться.");
    } finally {
      setCreatingEmployee(false);
    }
  }

  async function reissueCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!codeUserId || issuingCode) return;
    setIssuingCode(true);
    setEmployeeCode(null);
    setEmployeeMessage("");
    try {
      const response = await fetch("/api/max/employees", {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ userId: codeUserId }),
        cache: "no-store",
      });
      if (!response.ok) {
        setEmployeeMessage(response.status === 404
          ? "Сотрудник не найден, заблокирован или уже связал MAX. Обновите список."
          : response.status === 401 || response.status === 403
            ? "Доступ изменился. Проверьте вход снова."
            : "Не удалось выдать новый код. Попробуйте снова.");
        return;
      }
      const result: { status: "ISSUED"; token: string; expiresAt: string } = await response.json();
      setEmployeeCode({ token: result.token, expiresAt: result.expiresAt });
      setEmployeeMessage("Предыдущий код больше не действует.");
    } catch {
      setEmployeeMessage("Нет ответа от сервера. Перед повтором обновите вход: новый код мог уже сохраниться.");
    } finally {
      setIssuingCode(false);
    }
  }

  return <section className={styles.managerReport} aria-labelledby="manager-report-title">
    <h2 id="manager-report-title">Отчёт HR</h2>
    <p>Результаты, назначения и документы курсов вашей организации. Чтобы загрузить источник, откройте раздел и выберите курс.</p>
    <button type="button" className={styles.retry} onClick={() => setOpen((value) => !value)}>
      {open ? "Скрыть раздел HR" : "Открыть раздел HR"}
    </button>
    {open && <>
      {assignment?.canCreate && <details className={styles.launchHelp}>
        <summary>Как подключить сотрудника</summary>
        <ol>
          <li>Добавьте сотрудника ниже. Для уже созданного профиля выберите «Новый код для существующего сотрудника».</li>
          <li>Выберите курс и сотрудника в разделе «Назначить курс». Привязка сама по себе не назначает обучение.</li>
          <li>Передайте личный код сотруднику. Он открывает бота в MAX, нажимает «Открыть» и вводит код в мини-приложении.</li>
        </ol>
        <p>Код действует 15 минут. Сотрудник не регистрируется самостоятельно и не получает права HR. Результаты появятся в отчёте после прохождения.</p>
      </details>}
      {busy && <p role="status">Загружаем результаты…</p>}
      {message && <p role="alert">{message} <button type="button" className={styles.back} onClick={onRenew}>Проверить вход</button></p>}
      {assignment?.canCreate && <form className={styles.reportAssignment} onSubmit={(event) => void addEmployee(event)}>
        <h3>Добавить сотрудника</h3>
        <p>Создайте профиль своей организации. Почта на тестовом стенде не отправляется.</p>
        <label className={styles.reportSelect}>Имя
          <input value={employeeName} onChange={(event) => setEmployeeName(event.target.value)}
            required maxLength={80} autoComplete="given-name" />
        </label>
        <label className={styles.reportSelect}>Фамилия
          <input value={employeeLastName} onChange={(event) => setEmployeeLastName(event.target.value)}
            maxLength={80} autoComplete="family-name" />
        </label>
        <label className={styles.reportSelect}>Рабочий email
          <input type="email" value={employeeEmail} onChange={(event) => setEmployeeEmail(event.target.value)}
            required maxLength={254} autoComplete="email" />
        </label>
        <button type="submit" className={styles.retry} disabled={creatingEmployee}>
          {creatingEmployee ? "Добавляем…" : "Добавить"}
        </button>
      </form>}
      {assignment?.canCreate && assignment.learners.some((learner) => learner.canIssueCode) && <form className={styles.reportAssignment} onSubmit={(event) => void reissueCode(event)}>
        <h3>Новый код для существующего сотрудника</h3>
        <p>Если прежний код истёк или потерян, выдайте новый. После привязки MAX код больше не нужен.</p>
        <label className={styles.reportSelect}>Сотрудник
          <select value={codeUserId} onChange={(event) => setCodeUserId(event.target.value)} required>
            <option value="">Выберите сотрудника</option>
            {assignment.learners.filter((learner) => learner.canIssueCode).map((learner) =>
              <option key={learner.id} value={learner.id}>{learner.name}</option>)}
          </select>
        </label>
        <button type="submit" className={styles.retry} disabled={issuingCode || !codeUserId}>
          {issuingCode ? "Выдаём…" : "Выдать новый код"}
        </button>
      </form>}
      {assignment?.canCreate && employeeMessage && <p role={employeeCode ? "status" : "alert"}>{employeeMessage}</p>}
      {assignment?.canCreate && employeeCode && <div className={styles.employeeCode} role="status">
        <strong>Одноразовый код для сотрудника</strong>
        <code>{employeeCode.token}</code>
        <p>Передайте код только этому сотруднику. Он действует до {new Date(employeeCode.expiresAt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })} и вводится в Mini App MAX.</p>
      </div>}
      {!busy && !message && courses.length === 0 && <p>Пока нет опубликованных курсов вашей организации.</p>}
      {courses.length > 0 && <label className={styles.reportSelect}>Курс
        <select value={courseId} onChange={(event) => { setReport(null); setAssignmentMessage(""); setCourseId(event.target.value); }}>
          <option value="">Выберите курс</option>
          {courses.map((course) => <option key={course.id} value={course.id}>{course.title}</option>)}
        </select>
      </label>}
      {courseId && assignment?.canAssign && <form className={styles.reportAssignment} onSubmit={(event) => void assignCourse(event)}>
        <h3>Назначить курс</h3>
        <label className={styles.reportSelect}>Сотрудник
          <select value={learnerId} onChange={(event) => setLearnerId(event.target.value)} required>
            <option value="">Выберите сотрудника</option>
            {assignment.learners.map((learner) => <option key={learner.id} value={learner.id}>{learner.name}</option>)}
          </select>
        </label>
        {assignment.directoryTruncated && <p>Показаны первые 100 сотрудников. Остальных пока назначайте через LMS.</p>}
        <button type="submit" className={styles.retry} disabled={saving || !learnerId}>
          {saving ? "Сохраняем…" : "Назначить"}
        </button>
        {assignmentMessage && <p role="status">{assignmentMessage}</p>}
      </form>}
      {courseId && assignment?.canAssign && <MaxManagerDocuments key={courseId} courseId={courseId} token={token} onRenew={onRenew} />}
      {report && <div className={styles.reportResults}>
        <h3>{report.title}</h3>
        <p>Завершили: {report.completedCount} из {report.assignedCount}</p>
        {report.truncated && <p>Показаны первые 200 сотрудников. Для полного отчёта откройте LMS.</p>}
        {report.learners.length === 0 ? <p>Пока нет назначенных сотрудников вашей организации.</p>
          : <ul>{report.learners.map((learner) => <li key={learner.id}>
            <strong>{learner.name}</strong>
            <span>{learner.completed ? "Курс завершён" : "Не завершён"}
              {report.quizCount > 0 ? ` · тестов сдано: ${learner.passedQuizzes} из ${report.quizCount}` : ""}</span>
          </li>)}</ul>}
      </div>}
    </>}
  </section>;
}
