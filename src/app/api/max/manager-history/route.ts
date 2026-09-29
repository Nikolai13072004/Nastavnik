import {
  authenticateMaxRequest,
  isMaxId,
  maxPrivateHeaders as headers,
} from "@/modules/max/infrastructure/max-request";
import {
  getMaxLearnerHistory,
  getMaxQuestionErrors,
} from "@/modules/max/server/manager-learning-history";
import { getMaxManagerReport } from "@/modules/max/server/manager-report";
import { reportCsv } from "@/modules/max/application/hr-analytics";

export const runtime = "nodejs";

export async function GET(request: Request) {
  const identity = authenticateMaxRequest(request);
  if (!identity)
    return Response.json(
      { error: "SESSION_EXPIRED" },
      { status: 401, headers },
    );
  const params = new URL(request.url).searchParams;
  const learnerId = params.get("learnerId");
  const courseId = params.get("courseId");
  const page = Number(params.get("page") ?? "0");
  if (
    (!isMaxId(learnerId) && !isMaxId(courseId)) ||
    !Number.isInteger(page) ||
    page < 0 ||
    page > 10000
  ) {
    return Response.json(
      { error: "INVALID_REQUEST" },
      { status: 400, headers },
    );
  }
  try {
    if (params.get("export") === "csv" && isMaxId(courseId)) {
      const data = await getMaxManagerReport(identity, courseId);
      if ("error" in data)
        return Response.json(data, {
          status: data.error === "NOT_FOUND" ? 404 : 403,
          headers,
        });
      if (!data.report || data.report.truncated) {
        return Response.json(
          { error: "REPORT_TOO_LARGE" },
          { status: 409, headers },
        );
      }
      const csv = reportCsv([
        [
          "Курс",
          "Сотрудник",
          "Курс завершён",
          "Тестов сдано",
          "Тестов всего",
          "Срок обучения",
          "Документов к изучению",
        ],
        ...data.report.learners.map((learner) => [
          data.report.title,
          learner.name,
          learner.completed ? "Да" : "Нет",
          learner.passedQuizzes,
          data.report!.quizCount,
          learner.dueAt,
          learner.pendingDocuments,
        ]),
      ]);
      return new Response(csv, {
        headers: {
          ...headers,
          "Content-Type": "text/csv;charset=utf-8",
          "Content-Disposition": 'attachment; filename="learning-report.csv"',
        },
      });
    }
    const data = isMaxId(learnerId)
      ? await getMaxLearnerHistory(identity, learnerId, page)
      : await getMaxQuestionErrors(identity, courseId!);
    if ("error" in data)
      return Response.json(data, {
        status: data.error === "NOT_FOUND" ? 404 : 403,
        headers,
      });
    return Response.json(data, { headers });
  } catch {
    return Response.json(
      { error: "TEMPORARILY_UNAVAILABLE" },
      { status: 503, headers },
    );
  }
}
