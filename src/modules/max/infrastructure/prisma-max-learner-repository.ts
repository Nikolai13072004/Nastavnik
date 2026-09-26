import prisma from "@/lib/prisma";
import { parsePublishedCourseSnapshot } from "@/lib/course-content";
import type { MaxSessionIdentity } from "./learner-session";
import type { MaxLearnerRepository } from "../application/list-courses";

export async function findMaxSessionIdentity(maxUserId: string): Promise<MaxSessionIdentity | null> {
  const link = await prisma.maxAccountLink.findUnique({
    where: { maxUserId },
    select: { maxUserId: true, userId: true, organizationId: true, createdAt: true, user: { select: { status: true, organizationId: true } } },
  });
  if (!link || link.user.status !== "ACTIVE" || link.user.organizationId !== link.organizationId) return null;
  return { maxUserId, userId: link.userId, organizationId: link.organizationId, linkedAt: link.createdAt.toISOString() };
}

export const prismaMaxLearnerRepository: MaxLearnerRepository = {
  findIdentity: findMaxSessionIdentity,
  async findAssignedCourses(userId, organizationId) {
    const courses = await prisma.course.findMany({
      where: {
        status: "PUBLISHED",
        organizationId,
        OR: [
          { directAssignments: { some: { userId } } },
          { groupAssignments: { some: { group: { memberships: { some: { userId } } } } } },
        ],
      },
      orderBy: { id: "asc" },
      select: {
        id: true, title: true, publishedSnapshotJson: true,
        directAssignments: { where: { userId }, select: { expiresAt: true } },
        groupAssignments: { where: { group: { memberships: { some: { userId } } } }, select: { expiresAt: true } },
      },
    });
    return courses.map((course) => ({
      id: course.id,
      title: parsePublishedCourseSnapshot(course.publishedSnapshotJson)?.title ?? course.title,
      directExpiries: course.directAssignments.map((assignment) => assignment.expiresAt),
      groupExpiries: course.groupAssignments.map((assignment) => assignment.expiresAt),
    }));
  },
};
