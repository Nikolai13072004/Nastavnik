import { resolveEnrollmentAccess } from "@/modules/enrollment/domain/enrollment-access";

export type MaxLearnerIdentity = { maxUserId: string; userId: string; organizationId: string; linkedAt: string };
export type MaxCourse = { id: string; title: string; expiresAt: string | null };

export interface MaxLearnerRepository {
  findIdentity(maxUserId: string): Promise<MaxLearnerIdentity | null>;
  findAssignedCourses(userId: string, organizationId: string): Promise<Array<{
    id: string; title: string; directExpiries: Array<Date | null>; groupExpiries: Array<Date | null>;
  }>>;
}

export function createListMaxCourses(repository: MaxLearnerRepository) {
  return async (identity: MaxLearnerIdentity, now = new Date()): Promise<MaxCourse[] | null> => {
    const current = await repository.findIdentity(identity.maxUserId);
    if (!current || current.userId !== identity.userId || current.organizationId !== identity.organizationId || current.linkedAt !== identity.linkedAt) return null;
    const courses = await repository.findAssignedCourses(current.userId, current.organizationId);
    return courses.flatMap((course) => {
      const access = resolveEnrollmentAccess({ directExpiries: course.directExpiries, groupExpiries: course.groupExpiries, now });
      return access.isActive ? [{ id: course.id, title: course.title, expiresAt: access.expiresAt?.toISOString() ?? null }] : [];
    });
  };
}
