import "server-only";

import type { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { normalizePresentationViewMode } from "@/lib/constants";
import type {
  CourseCreationRepository,
  CourseCreationTransaction,
  CreateCourseItemInput,
} from "../application/creation-ports";

function createTransaction(
  client: Prisma.TransactionClient,
): CourseCreationTransaction {
  return {
    async createCourse(data) {
      const owner = await client.user.findUnique({
        where: { id: data.ownerId },
        select: { organizationId: true },
      });
      const created = await client.course.create({
        data: { ...data, organizationId: owner?.organizationId ?? null },
        select: { id: true, title: true },
      });
      return created;
    },

    async createModule(data) {
      const created = await client.courseModule.create({
        data: {
          courseId: data.courseId,
          title: data.title,
          description: data.description ?? null,
          orderIndex: data.orderIndex,
        },
        select: { id: true },
      });
      return created;
    },

    async createItem(data: CreateCourseItemInput) {
      const created = await client.courseItem.create({
        data: {
          courseId: data.courseId,
          moduleId: data.moduleId,
          orderIndex: data.orderIndex,
          type: data.type,
          title: data.title,
          content: data.content,
          fileUrl: data.fileUrl,
          totalSlides: data.totalSlides,
          presentationViewMode: normalizePresentationViewMode(
            data.presentationViewMode,
          ),
          isRequired: data.isRequired,
          quiz: data.quiz
            ? {
                create: {
                  description: data.quiz.description,
                  maxAttempts: data.quiz.maxAttempts,
                  minCorrectAnswers: data.quiz.minCorrectAnswers,
                  questions:
                    data.quiz.questions.length > 0
                      ? {
                          create: data.quiz.questions.map((question) => ({
                            orderIndex: question.orderIndex,
                            type: question.type,
                            prompt: question.prompt,
                            config: question.config,
                            points: question.points,
                          })),
                        }
                      : undefined,
                },
              }
            : undefined,
        },
        include: { quiz: { select: { id: true } } },
      });
      return { id: created.id, quizId: created.quiz?.id ?? null };
    },

    async recordEffects(effects) {
      if (!effects.audit) return;
      const audit = effects.audit;
      await client.auditLogEvent.create({
        data: {
          actorId: audit.actorId,
          actorLogin: audit.actorLogin,
          actorName: audit.actorName,
          action: audit.action,
          objectType: audit.objectType,
          objectId: audit.objectId,
          objectLabel: audit.objectLabel,
          ipAddress: audit.ipAddress,
          userAgent: audit.userAgent,
          metadataJson:
            audit.metadata === undefined
              ? null
              : JSON.stringify(audit.metadata),
        },
      });
    },
  };
}

export const prismaCourseCreationRepository: CourseCreationRepository = {
  async findCourseForCopy(courseId) {
    const course = await prisma.course.findUnique({
      where: { id: courseId },
      include: {
        modules: {
          where: { archivedAt: null },
          orderBy: { orderIndex: "asc" },
        },
        items: {
          where: { archivedAt: null },
          orderBy: { orderIndex: "asc" },
          include: {
            quiz: {
              include: {
                questions: {
                  where: { archivedAt: null },
                  orderBy: { orderIndex: "asc" },
                },
              },
            },
          },
        },
      },
    });
    if (!course) return null;
    return {
      id: course.id,
      title: course.title,
      description: course.description,
      requirements: course.requirements,
      targetAudience: course.targetAudience,
      category: course.category,
      difficultyLevel: course.difficultyLevel,
      durationMinutes: course.durationMinutes,
      tagsJson: course.tagsJson,
      thumbnailUrl: course.thumbnailUrl,
      coverUrl: course.coverUrl,
      navigationMode: course.navigationMode,
      quizGateMode: course.quizGateMode,
      completionMode: course.completionMode,
      statusFormat: course.statusFormat,
      gradedItemIdsJson: course.gradedItemIdsJson,
      resultViewMode: course.resultViewMode,
      modules: course.modules.map((module) => ({
        id: module.id,
        title: module.title,
        description: module.description,
        orderIndex: module.orderIndex,
      })),
      items: course.items.map((item) => ({
        id: item.id,
        moduleId: item.moduleId,
        orderIndex: item.orderIndex,
        type: item.type,
        title: item.title,
        content: item.content,
        fileUrl: item.fileUrl,
        totalSlides: item.totalSlides,
        presentationViewMode: item.presentationViewMode,
        isRequired: item.isRequired,
        quiz: item.quiz
          ? {
              description: item.quiz.description,
              maxAttempts: item.quiz.maxAttempts,
              minCorrectAnswers: item.quiz.minCorrectAnswers,
              questions: item.quiz.questions.map((question) => ({
                orderIndex: question.orderIndex,
                type: question.type,
                prompt: question.prompt,
                config: question.config,
                points: question.points,
              })),
            }
          : null,
      })),
    };
  },

  async transact(execute) {
    return prisma.$transaction((client) => execute(createTransaction(client)));
  },
};
