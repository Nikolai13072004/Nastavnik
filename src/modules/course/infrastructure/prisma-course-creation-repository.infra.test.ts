import assert from "node:assert/strict";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { prismaCourseCreationRepository as repo } from "./prisma-course-creation-repository";

// Инфра-тест репозитория создания курсов против настоящей БД.
// Запуск: npm run test:infra.

after(async () => {
  await prisma.$disconnect();
});

async function seedOwner(id: string) {
  return prisma.user.create({
    data: { id, login: `login-${id}`, name: `User ${id}`, passwordHash: "x" },
  });
}

test("createCourse: сохраняет базовые поля и вернёт id + title", async () => {
  const owner = await seedOwner("cc-owner-1");
  const created = await repo.transact(async (tx) =>
    tx.createCourse({
      title: "CC Test",
      description: "desc",
      category: "Дизайн",
      difficultyLevel: "EASY",
      durationMinutes: 60,
      thumbnailUrl: null,
      coverUrl: null,
      ownerId: owner.id,
    }),
  );
  const row = await prisma.course.findUnique({ where: { id: created.id } });
  assert.equal(row?.title, "CC Test");
  assert.equal(row?.durationMinutes, 60);
});

test("createCourse: фиксирует организацию на момент создания, а не выводит её из текущего владельца", async () => {
  const organization = await prisma.organization.create({ data: { name: "CC organization scope" } });
  const owner = await prisma.user.create({
    data: { login: "cc-org-owner", name: "Organization owner", passwordHash: "x", organizationId: organization.id },
  });
  const created = await repo.transact((tx) => tx.createCourse({
    title: "Scoped course", description: "desc", category: null, difficultyLevel: null,
    durationMinutes: null, thumbnailUrl: null, coverUrl: null, ownerId: owner.id,
  }));
  assert.equal((await prisma.course.findUniqueOrThrow({ where: { id: created.id } })).organizationId, organization.id);

  await prisma.user.update({ where: { id: owner.id }, data: { organizationId: null } });
  assert.equal((await prisma.course.findUniqueOrThrow({ where: { id: created.id } })).organizationId, organization.id);
});

test("createItem с nested quiz + questions создаёт всё одной операцией", async () => {
  const owner = await seedOwner("cc-owner-2");
  const course = await repo.transact(async (tx) =>
    tx.createCourse({
      title: "CC Quiz",
      description: "d",
      category: null,
      difficultyLevel: null,
      durationMinutes: null,
      thumbnailUrl: null,
      coverUrl: null,
      ownerId: owner.id,
    }),
  );
  const courseModule = await repo.transact(async (tx) =>
    tx.createModule({ courseId: course.id, title: "M", orderIndex: 0 }),
  );
  const item = await repo.transact(async (tx) =>
    tx.createItem({
      courseId: course.id,
      moduleId: courseModule.id,
      orderIndex: 0,
      type: "QUIZ",
      title: "Quiz item",
      content: null,
      fileUrl: null,
      totalSlides: null,
      presentationViewMode: "PDF_PREVIEW",
      isRequired: true,
      quiz: {
        description: "d",
        maxAttempts: 3,
        minCorrectAnswers: 2,
        questions: [
          {
            orderIndex: 0,
            type: "SINGLE_CHOICE",
            prompt: "Q1",
            config: "{}",
            points: 1,
          },
        ],
      },
    }),
  );
  assert.ok(item.quizId);
  const questions = await prisma.question.findMany({
    where: { quizId: item.quizId ?? "" },
  });
  assert.equal(questions.length, 1);
  assert.equal(questions[0].prompt, "Q1");
});

test("createItem без quiz: quizId=null", async () => {
  const owner = await seedOwner("cc-owner-3");
  const course = await repo.transact(async (tx) =>
    tx.createCourse({
      title: "CC Text",
      description: "d",
      category: null,
      difficultyLevel: null,
      durationMinutes: null,
      thumbnailUrl: null,
      coverUrl: null,
      ownerId: owner.id,
    }),
  );
  const item = await repo.transact(async (tx) =>
    tx.createItem({
      courseId: course.id,
      moduleId: null,
      orderIndex: 0,
      type: "TEXT",
      title: "Text item",
      content: "<p>hi</p>",
      fileUrl: null,
      totalSlides: null,
      presentationViewMode: "PDF_PREVIEW",
      isRequired: true,
      quiz: null,
    }),
  );
  assert.equal(item.quizId, null);
});

test("findCourseForCopy: полный snapshot включая modules/items/quiz/questions", async () => {
  const owner = await seedOwner("cc-owner-copy");
  const course = await repo.transact(async (tx) =>
    tx.createCourse({
      title: "Src Course",
      description: "src",
      category: "Дизайн",
      difficultyLevel: null,
      durationMinutes: 30,
      thumbnailUrl: null,
      coverUrl: null,
      ownerId: owner.id,
    }),
  );
  const courseModule = await repo.transact(async (tx) =>
    tx.createModule({
      courseId: course.id,
      title: "Src M",
      description: "descr",
      orderIndex: 0,
    }),
  );
  await repo.transact(async (tx) =>
    tx.createItem({
      courseId: course.id,
      moduleId: courseModule.id,
      orderIndex: 0,
      type: "QUIZ",
      title: "Src Quiz",
      content: null,
      fileUrl: null,
      totalSlides: null,
      presentationViewMode: "PDF_PREVIEW",
      isRequired: true,
      quiz: {
        description: "quiz d",
        maxAttempts: 1,
        minCorrectAnswers: 1,
        questions: [
          {
            orderIndex: 0,
            type: "SINGLE_CHOICE",
            prompt: "Q1",
            config: "{}",
            points: 2,
          },
        ],
      },
    }),
  );

  const snapshot = await repo.findCourseForCopy(course.id);
  assert.ok(snapshot);
  assert.equal(snapshot.modules.length, 1);
  assert.equal(snapshot.modules[0].title, "Src M");
  assert.equal(snapshot.items.length, 1);
  assert.equal(snapshot.items[0].quiz?.questions.length, 1);
  assert.equal(snapshot.items[0].quiz?.questions[0].points, 2);
});

test("findCourseForCopy: missing → null", async () => {
  const snapshot = await repo.findCourseForCopy("cc-missing");
  assert.equal(snapshot, null);
});

test("transact атомарна: throw откатывает createCourse", async () => {
  const owner = await seedOwner("cc-owner-tx");
  await assert.rejects(
    repo.transact(async (tx) => {
      await tx.createCourse({
        title: "Roll Back",
        description: "d",
        category: null,
        difficultyLevel: null,
        durationMinutes: null,
        thumbnailUrl: null,
        coverUrl: null,
        ownerId: owner.id,
      });
      throw new Error("boom");
    }),
    /boom/,
  );
  const found = await prisma.course.findFirst({
    where: { title: "Roll Back", ownerId: owner.id },
  });
  assert.equal(found, null, "курс не создался");
});
