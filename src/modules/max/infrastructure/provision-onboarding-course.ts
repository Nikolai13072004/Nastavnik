import { createHash } from "node:crypto";
import { Prisma, type PrismaClient } from "@prisma/client";
import { buildPublishedCourseSnapshot } from "@/lib/course-content";

export const onboardingCourseId = "max-pilot-onboarding-course";
export const onboardingMaterialId = "max-pilot-onboarding-material";
export const onboardingQuizId = "max-pilot-onboarding-quiz";
export const onboardingDocumentId = "max-pilot-onboarding-document";
const organizationId = "max-pilot-demo-org";
const learnerId = "max-pilot-demo-learner";
const sourceCourseId = "max-pilot-demo-course";
// Owner-approved bytes already used by the pilot. Changed text needs a new review.
const approvedHash =
  "4ae1eaa63cc6a90372bde88a52d1c94c573c14ef701f7fac262cfc52199acdd2";

export async function provisionOnboardingCourse(db: PrismaClient) {
  return db.$transaction(
    async (tx) => {
      const learner = await tx.user.findUnique({ where: { id: learnerId } });
      if (
        learner?.organizationId !== organizationId ||
        learner.status !== "ACTIVE" ||
        learner.login !== "max-pilot-demo"
      ) {
        throw new Error("Expected active pilot learner");
      }
      const source = await tx.maxCourseDocument.findFirst({
        where: {
          organizationId,
          courseId: sourceCourseId,
          sourceName: "onboarding-policy.md",
          contentHash: approvedHash,
          approvedAt: { not: null },
          revokedAt: null,
        },
      });
      if (
        !source ||
        createHash("sha256").update(source.contentText).digest("hex") !==
          approvedHash
      ) {
        throw new Error("Expected the unchanged, approved onboarding document");
      }
      const existing = await tx.course.findUnique({
        where: { id: onboardingCourseId },
        include: {
          items: { include: { quiz: { include: { questions: true } } } },
          maxCourseDocuments: true,
          directAssignments: { where: { userId: learnerId } },
        },
      });
      if (existing) {
        const document = existing.maxCourseDocuments.find(
          (item) => item.id === onboardingDocumentId,
        );
        if (
          existing.organizationId !== organizationId ||
          existing.status !== "PUBLISHED" ||
          !existing.publishedSnapshotJson ||
          existing.directAssignments.length !== 1 ||
          !existing.items.some(
            (item) => item.id === onboardingMaterialId && item.isRequired,
          ) ||
          !existing.items.some(
            (item) =>
              item.quiz?.id === onboardingQuizId &&
              item.isRequired &&
              item.quiz.questions.length === 3,
          ) ||
          document?.contentHash !== approvedHash ||
          !document.approvedAt ||
          document.revokedAt ||
          document.contentText !== source.contentText
        ) {
          throw new Error(
            "Existing onboarding course differs; no data was changed",
          );
        }
        return "ALREADY_EXISTS" as const;
      }

      await tx.course.create({
        data: {
          id: onboardingCourseId,
          organizationId,
          ownerId: learnerId,
          title: "Первый день сотрудника в MAX",
          description:
            "Прочитайте материал, задайте вопрос по учебному документу и пройдите три вопроса. Все результаты сохраняются в этом курсе.",
          category: "Адаптация",
          durationMinutes: 5,
          quizGateMode: "PASSED",
        },
      });
      await tx.courseItem.create({
        data: {
          id: onboardingMaterialId,
          courseId: onboardingCourseId,
          type: "TEXT",
          title: "Доступ, документы и проверка знаний",
          orderIndex: 0,
          isRequired: true,
          content:
            "<p>Это учебный пример с вымышленными данными.</p><p>HR назначает курс и передаёт одноразовый код привязки. Код вводят внутри мини-приложения, его нельзя пересылать другому человеку. Если код потерян или не работает, обратитесь к HR за новым.</p><p>Изучите опубликованный материал и подтвердите чтение. Ниже можно задать вопрос по учебному документу. Ответ AI проверяйте по источнику. Если подтверждённого ответа нет, обратитесь к HR или руководителю.</p><p>Затем пройдите тест из трёх вопросов. Руководитель увидит результат в Наставнике. Для конкурсного курса нельзя использовать реальные персональные данные, пароли, токены или конфиденциальные документы.</p>",
        },
      });
      await tx.courseItem.create({
        data: {
          id: "max-pilot-onboarding-check",
          courseId: onboardingCourseId,
          type: "QUIZ",
          title: "Проверка знаний",
          orderIndex: 1,
          isRequired: true,
          quiz: {
            create: {
              id: onboardingQuizId,
              maxAttempts: 3,
              minCorrectAnswers: 3,
              lockMaterialsOnStart: false,
              description:
                "Три вопроса по материалу. Для сдачи ответьте на все верно.",
              questions: {
                create: [
                  {
                    orderIndex: 0,
                    type: "SINGLE_CHOICE",
                    points: 1,
                    prompt: "Что делать, если код привязки потерян?",
                    config: JSON.stringify({
                      options: [
                        "Попросить чужой код",
                        "Обратиться к HR за новым кодом",
                        "Отправить пароль в чат",
                      ],
                      correctIndex: 1,
                    }),
                  },
                  {
                    orderIndex: 1,
                    type: "SINGLE_CHOICE",
                    points: 1,
                    prompt: "Как проверить ответ AI по документу?",
                    config: JSON.stringify({
                      options: [
                        "Открыть источник и сверить текст",
                        "Доверять любому ответу",
                        "Посмотреть только длину ответа",
                      ],
                      correctIndex: 0,
                    }),
                  },
                  {
                    orderIndex: 2,
                    type: "SINGLE_CHOICE",
                    points: 1,
                    prompt:
                      "Какие сведения допустимы в конкурсном учебном курсе?",
                    config: JSON.stringify({
                      options: [
                        "Пароли сотрудников",
                        "Конфиденциальные документы",
                        "Вымышленные сведения и подготовленные материалы",
                      ],
                      correctIndex: 2,
                    }),
                  },
                ],
              },
            },
          },
        },
      });
      const course = await tx.course.findUniqueOrThrow({
        where: { id: onboardingCourseId },
        include: {
          modules: true,
          items: { include: { quiz: { include: { questions: true } } } },
        },
      });
      await tx.course.update({
        where: { id: onboardingCourseId },
        data: {
          status: "PUBLISHED",
          publishedAt: new Date(),
          publishedSnapshotJson: JSON.stringify(
            buildPublishedCourseSnapshot(course),
          ),
        },
      });
      await tx.maxCourseDocument.create({
        data: {
          id: onboardingDocumentId,
          organizationId,
          courseId: onboardingCourseId,
          title: source.title,
          sourceName: source.sourceName,
          contentText: source.contentText,
          contentHash: source.contentHash,
          versionNumber: source.versionNumber,
          uploadedById: source.uploadedById,
          approvedById: source.approvedById,
          approvedAt: source.approvedAt,
        },
      });
      await tx.courseUserAssignment.create({
        data: { courseId: onboardingCourseId, userId: learnerId },
      });
      await tx.auditLogEvent.create({
        data: {
          actorLogin: "system:max-pilot-course",
          actorName: "Pilot course setup",
          action: "max_course:provision",
          objectType: "course",
          objectId: onboardingCourseId,
          objectLabel: course.title,
          metadataJson: JSON.stringify({
            organizationId,
            sourceCourseId,
            sourceDocumentId: source.id,
            contentHash: approvedHash,
            approval: "unchanged-owner-approved-text",
          }),
        },
      });
      return "CREATED" as const;
    },
    { isolationLevel: Prisma.TransactionIsolationLevel.Serializable },
  );
}
