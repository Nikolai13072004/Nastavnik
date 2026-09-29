/** One-time, narrowly scoped demonstration data for the isolated MAX pilot. */
import { createHash, randomBytes } from "node:crypto";
import { hash } from "bcryptjs";
import { PrismaClient } from "@prisma/client";
import { buildPublishedCourseSnapshot } from "../src/lib/course-content";

const prisma = new PrismaClient();
const organizationId = "max-pilot-demo-org";
const userId = "max-pilot-demo-learner";
const courseId = "max-pilot-demo-course";
const itemId = "max-pilot-demo-intro";
const assessmentCourseId = "max-pilot-assessment-course";
const assessmentMaterialId = "max-pilot-assessment-intro";
const assessmentItemId = "max-pilot-assessment-item";
const assessmentQuizId = "max-pilot-assessment-quiz";

async function setup() {
  const existing = await prisma.user.findUnique({ where: { id: userId } });
  if (existing) {
    if (existing.login !== "max-pilot-demo" || existing.organizationId !== organizationId) {
      throw new Error("Pilot user ID is occupied by other data");
    }
    const course = await prisma.course.findUnique({ where: { id: courseId } });
    if (!course || course.status !== "PUBLISHED") throw new Error("Pilot setup is incomplete");
    console.log("PILOT_DATA_ALREADY_EXISTS");
    return;
  }

  const [users, courses] = await Promise.all([prisma.user.count(), prisma.course.count()]);
  if (users !== 0 || courses !== 0) throw new Error("Pilot database is no longer empty");

  const passwordHash = await hash(randomBytes(32).toString("base64url"), 12);
  await prisma.$transaction(async (tx) => {
    await tx.organization.create({ data: { id: organizationId, name: "Наставник · демонстрация" } });
    await tx.user.create({
      data: {
        id: userId,
        login: "max-pilot-demo",
        name: "Илья · демонстрационный профиль",
        firstName: "Илья",
        passwordHash,
        role: "USER",
        status: "ACTIVE",
        organizationId,
      },
    });
    await tx.course.create({
      data: {
        id: courseId,
        title: "Первый день: обучение в MAX",
        description: "Короткий демонстрационный курс о том, как работает учебный сценарий.",
        category: "Адаптация",
        durationMinutes: 3,
        ownerId: userId,
        organizationId,
      },
    });
    await tx.courseItem.create({
      data: {
        id: itemId,
        courseId,
        type: "TEXT",
        title: "Как устроено обучение",
        orderIndex: 0,
        isRequired: true,
        content: "<p>Это демонстрационный материал, а не регламент компании.</p><p>Сотрудник открывает назначенный курс в MAX, изучает материал и проходит проверку знаний. Руководитель видит результат в Наставнике.</p><p>Если информация в рабочем документе изменилась, новую версию и вопросы должен подтвердить ответственный сотрудник.</p>",
      },
    });
    const course = await tx.course.findUniqueOrThrow({
      where: { id: courseId },
      include: { modules: true, items: { include: { quiz: { include: { questions: true } } } } },
    });
    await tx.course.update({
      where: { id: courseId },
      data: {
        status: "PUBLISHED",
        publishedAt: new Date(),
        publishedSnapshotJson: JSON.stringify(buildPublishedCourseSnapshot(course)),
      },
    });
    await tx.courseUserAssignment.create({ data: { courseId, userId } });
  });
  console.log("PILOT_ORGANIZATION_LEARNER_COURSE_READY");
}

async function issueInvite() {
  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: { organizationId: true, status: true, maxLink: { select: { maxUserId: true } } },
  });
  if (!user || user.organizationId !== organizationId || user.status !== "ACTIVE") {
    throw new Error("Pilot learner is unavailable");
  }
  if (user.maxLink) {
    console.log("PILOT_LEARNER_ALREADY_LINKED");
    return;
  }
  const token = randomBytes(24).toString("base64url");
  const expiresAt = new Date(Date.now() + 15 * 60_000);
  await prisma.maxLinkInvite.upsert({
    where: { userId },
    create: {
      userId,
      organizationId,
      tokenHash: createHash("sha256").update(token).digest("hex"),
      expiresAt,
    },
    update: {
      organizationId,
      tokenHash: createHash("sha256").update(token).digest("hex"),
      expiresAt,
    },
  });
  console.log(`PILOT_INVITE=${token}`);
  console.log(`EXPIRES_AT=${expiresAt.toISOString()}`);
}

async function setupAssessment() {
  const existing = await prisma.course.findUnique({ where: { id: assessmentCourseId } });
  if (existing) {
    if (existing.status !== "PUBLISHED") throw new Error("Assessment pilot course is incomplete");
    console.log("PILOT_ASSESSMENT_ALREADY_EXISTS");
    return;
  }
  const learner = await prisma.user.findUnique({ where: { id: userId } });
  if (!learner || learner.organizationId !== organizationId) throw new Error("Pilot learner is unavailable");

  await prisma.$transaction(async (tx) => {
    await tx.course.create({
      data: {
        id: assessmentCourseId,
        title: "Демо: материал и проверка знаний",
        description: "Отдельный демонстрационный курс: изучите короткий материал и ответьте на два вопроса.",
        category: "Адаптация",
        durationMinutes: 4,
        organizationId,
      },
    });
    await tx.courseItem.create({
      data: {
        id: assessmentMaterialId,
        courseId: assessmentCourseId,
        type: "TEXT",
        title: "Правило работы с документами",
        orderIndex: 0,
        isRequired: true,
        content: "<p>Это демонстрационный материал, а не настоящий регламент компании.</p><p>Изменение рабочего документа перед публикацией подтверждает ответственный сотрудник. AI может предложить черновик, но не утверждает его самостоятельно.</p><p>Если сотрудник задаёт вопрос по внутреннему документу, ответ должен содержать источник, чтобы его можно было проверить.</p>",
      },
    });
    await tx.courseItem.create({
      data: {
        id: assessmentItemId,
        courseId: assessmentCourseId,
        type: "QUIZ",
        title: "Проверка знаний",
        orderIndex: 1,
        isRequired: true,
      },
    });
    await tx.quiz.create({
      data: {
        id: assessmentQuizId,
        courseItemId: assessmentItemId,
        description: "Два вопроса по демонстрационному материалу. Для сдачи ответьте на оба верно.",
        maxAttempts: 3,
        minCorrectAnswers: 2,
        lockMaterialsOnStart: false,
        questions: {
          create: [
            {
              orderIndex: 0,
              type: "SINGLE_CHOICE",
              prompt: "Кто утверждает изменение рабочего документа перед публикацией?",
              config: JSON.stringify({ options: ["AI-система", "Ответственный сотрудник", "Любой участник курса"], correctIndex: 1 }),
              points: 1,
            },
            {
              orderIndex: 1,
              type: "SINGLE_CHOICE",
              prompt: "Что необходимо в ответе по внутреннему документу?",
              config: JSON.stringify({ options: ["Проверяемый источник", "Только краткий вывод", "Ответ без ссылок"], correctIndex: 0 }),
              points: 1,
            },
          ],
        },
      },
    });
    const course = await tx.course.findUniqueOrThrow({
      where: { id: assessmentCourseId },
      include: { modules: true, items: { include: { quiz: { include: { questions: true } } } } },
    });
    await tx.course.update({
      where: { id: assessmentCourseId },
      data: {
        status: "PUBLISHED",
        publishedAt: new Date(),
        publishedSnapshotJson: JSON.stringify(buildPublishedCourseSnapshot(course)),
      },
    });
    await tx.courseUserAssignment.create({ data: { courseId: assessmentCourseId, userId } });
  });
  console.log("PILOT_ASSESSMENT_READY");
}

async function scopeExistingPilotCourses() {
  const learner = await prisma.user.findUnique({
    where: { id: userId },
    select: { login: true, status: true, organizationId: true },
  });
  if (!learner || learner.login !== "max-pilot-demo" ||
      learner.status !== "ACTIVE" || learner.organizationId !== organizationId) {
    throw new Error("Pilot learner does not match the expected demo organization");
  }

  const expected = [
    { id: courseId, title: "Первый день: обучение в MAX", ownerId: userId },
    { id: assessmentCourseId, title: "Демо: материал и проверка знаний", ownerId: null },
  ];
  await prisma.$transaction(async (tx) => {
    for (const course of expected) {
      const current = await tx.course.findUnique({
        where: { id: course.id },
        select: {
          title: true, status: true, ownerId: true, organizationId: true,
          directAssignments: { where: { userId }, select: { id: true } },
        },
      });
      if (!current || current.title !== course.title || current.status !== "PUBLISHED" ||
          current.ownerId !== course.ownerId || current.directAssignments.length !== 1 ||
          (current.organizationId !== null && current.organizationId !== organizationId)) {
        throw new Error("Pilot course does not match the expected demo data");
      }
      if (current.organizationId === null) {
        await tx.course.update({
          where: { id: course.id },
          data: { organizationId },
        });
      }
    }
  });
  console.log("PILOT_COURSE_ORGANIZATIONS_CONFIRMED");
}

const command = process.argv[2];
(command === "setup" ? setup()
  : command === "invite" ? issueInvite()
  : command === "assessment" ? setupAssessment()
  : command === "scope" ? scopeExistingPilotCourses()
  : Promise.reject(new Error("Expected setup, invite, assessment or scope")))
  .catch(() => {
    console.error("Pilot setup failed; review existing pilot data and configuration.");
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
