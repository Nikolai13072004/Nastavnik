import type { MaxLearnerIdentity } from "./list-courses";
import type { AuthorizedDocument } from "./verify-knowledge-answer";

export interface MaxKnowledgeAccessPolicy {
  publishedSnapshotHash(identity: MaxLearnerIdentity, courseId: string): Promise<string | null>;
}

export interface MaxKnowledgeDocumentRepository {
  listApproved(organizationId: string, courseId: string, snapshotHash: string): Promise<AuthorizedDocument[]>;
}

export function createListAuthorizedKnowledgeDocuments(
  access: MaxKnowledgeAccessPolicy,
  repository: MaxKnowledgeDocumentRepository,
) {
  return async (identity: MaxLearnerIdentity, courseId: string): Promise<AuthorizedDocument[] | null> => {
    const snapshotHash = await access.publishedSnapshotHash(identity, courseId);
    if (!snapshotHash) return null;
    return repository.listApproved(identity.organizationId, courseId, snapshotHash);
  };
}
