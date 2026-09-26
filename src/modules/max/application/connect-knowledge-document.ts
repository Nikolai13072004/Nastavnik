import type { MaxLearnerIdentity } from "./list-courses";

export type KnowledgeConnectionResult =
  | "APPROVED" | "FORBIDDEN" | "NOT_FOUND" | "NOT_APPROVED" | "UNSUPPORTED_FORMAT"
  | "HASH_MISMATCH" | "CONFLICT" | "SOURCE_NOT_READY";

type PublishedDocument = {
  sourceName: string;
  contentHash: string;
  publishedSnapshotHash: string;
};

export interface KnowledgeConnectionRepository {
  load(identity: MaxLearnerIdentity, courseId: string, documentId: string): Promise<PublishedDocument | null>;
  approve(identity: MaxLearnerIdentity, courseId: string, documentId: string,
    source: { documentId: string; documentHash: string }, snapshotHash: string): Promise<KnowledgeConnectionResult>;
}

export function createConnectKnowledgeDocument(repository: KnowledgeConnectionRepository, client: {
  findDocument(organizationId: string, courseId: string, contentHash: string):
    Promise<{ documentId: string; documentHash: string } | null>;
}) {
  return async (identity: MaxLearnerIdentity, courseId: string, documentId: string) => {
    const document = await repository.load(identity, courseId, documentId);
    if (!document) return "NOT_FOUND" as const;
    if (!/\.(txt|md)$/i.test(document.sourceName)) return "UNSUPPORTED_FORMAT" as const;
    const source = await client.findDocument(identity.organizationId, courseId, document.contentHash);
    if (!source) return "SOURCE_NOT_READY" as const;
    if (source.documentHash !== document.contentHash) return "HASH_MISMATCH" as const;
    return repository.approve(identity, courseId, documentId, source, document.publishedSnapshotHash);
  };
}
