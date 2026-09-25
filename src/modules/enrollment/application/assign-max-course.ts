export type MaxAssignmentIdentity = {
  maxUserId: string;
  userId: string;
  organizationId: string;
  linkedAt: string;
};

export type MaxAssignmentActor = {
  id: string;
  login: string;
  name: string;
  organizationId: string;
};

export type MaxAssignmentTransaction = {
  findActor(identity: MaxAssignmentIdentity): Promise<MaxAssignmentActor | null>;
  findCourse(courseId: string, organizationId: string): Promise<{ id: string; title: string } | null>;
  findLearner(learnerId: string, organizationId: string): Promise<{ id: string } | null>;
  directExpiry(courseId: string, learnerId: string): Promise<Date | null | undefined>;
  createDirect(courseId: string, learnerId: string, actorId: string): Promise<boolean>;
  renewDirect(courseId: string, learnerId: string, actorId: string, now: Date): Promise<boolean>;
  assignCurrentDocumentTrainings(courseId: string, learnerId: string, organizationId: string): Promise<void>;
  recordAudit(actor: MaxAssignmentActor, course: { id: string; title: string }, learnerId: string, renewed: boolean): Promise<void>;
};

export type MaxAssignmentRepository = {
  transact<T>(work: (transaction: MaxAssignmentTransaction) => Promise<T>): Promise<T>;
};

export function createAssignMaxCourse(repository: MaxAssignmentRepository, clock = () => new Date()) {
  return async function assignMaxCourse(identity: MaxAssignmentIdentity, courseId: string, learnerId: string) {
    return repository.transact(async (transaction) => {
      const actor = await transaction.findActor(identity);
      if (!actor) return { status: "FORBIDDEN" as const };

      const [course, learner] = await Promise.all([
        transaction.findCourse(courseId, actor.organizationId),
        transaction.findLearner(learnerId, actor.organizationId),
      ]);
      if (!course || !learner) return { status: "NOT_FOUND" as const };

      const now = clock();
      const currentExpiry = await transaction.directExpiry(course.id, learner.id);
      if (currentExpiry === null || (currentExpiry && currentExpiry > now)) {
        return { status: "ALREADY_ASSIGNED" as const };
      }

      const renewed = currentExpiry !== undefined;
      const saved = renewed
        ? await transaction.renewDirect(course.id, learner.id, actor.id, now)
        : await transaction.createDirect(course.id, learner.id, actor.id);
      if (!saved) return { status: "ALREADY_ASSIGNED" as const };

      await transaction.assignCurrentDocumentTrainings(course.id, learner.id, actor.organizationId);
      await transaction.recordAudit(actor, course, learner.id, renewed);
      return { status: renewed ? "RENEWED" as const : "ASSIGNED" as const };
    });
  };
}
