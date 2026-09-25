import type { MaxLearnerIdentity } from "./list-courses";
import { verifyKnowledgeAnswer, type AuthorizedDocument, type KnowledgeAnswer } from "./verify-knowledge-answer";

export interface KnowledgeAuthorization {
  list(identity: MaxLearnerIdentity, courseId: string): Promise<AuthorizedDocument[] | null>;
}

export interface KnowledgeService {
  ask(organizationId: string, courseId: string, question: string): Promise<KnowledgeAnswer>;
}

export function createAskAuthorizedKnowledgeQuestion(
  authorization: KnowledgeAuthorization,
  service: KnowledgeService,
) {
  return async (identity: MaxLearnerIdentity, courseId: string, question: string): Promise<KnowledgeAnswer | null> => {
    const approvedBefore = await authorization.list(identity, courseId);
    if (!approvedBefore?.length) return null;

    const answer = await service.ask(identity.organizationId, courseId, question);

    // Access, the published course, or an approved document may change while Vedomo is answering.
    const approvedAfter = await authorization.list(identity, courseId);
    if (!approvedAfter?.length) return null;

    return verifyKnowledgeAnswer(verifyKnowledgeAnswer(answer, approvedBefore), approvedAfter);
  };
}
