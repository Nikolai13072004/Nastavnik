import type { MaxLearnerIdentity } from "./list-courses";

export type KnowledgeConnectionResult =
  | "APPROVED" | "FORBIDDEN" | "NOT_FOUND" | "NOT_APPROVED" | "UNSUPPORTED_FORMAT"
  | "HASH_MISMATCH" | "CONFLICT" | "SOURCE_NOT_READY"
  | "PROCESSING" | "SOURCE_BUSY" | "INDEXING_FAILED";

type PublishedDocument = {
  sourceName: string;
  contentHash: string;
  contentText?: string;
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
  importDocument?(organizationId: string, courseId: string, document: {
    sourceName: string; contentText: string; contentHash: string; retry: boolean;
  }): Promise<{ status: "READY" | "PROCESSING" | "BUSY" | "ERROR"; documentId?: string; documentHash: string }>;
}) {
  return async (identity: MaxLearnerIdentity, courseId: string, documentId: string, retry = false) => {
    const document = await repository.load(identity, courseId, documentId);
    if (!document) return "NOT_FOUND" as const;
    if (!/\.(txt|md|pdf)$/i.test(document.sourceName) ||
        (!client.importDocument && /\.pdf$/i.test(document.sourceName))) return "UNSUPPORTED_FORMAT" as const;
    let source;
    if (client.importDocument) {
      if (!document.contentText) return "HASH_MISMATCH" as const;
      const imported = await client.importDocument(identity.organizationId, courseId, {
        sourceName: /\.pdf$/i.test(document.sourceName) ? `max-source-${documentId}.txt` : document.sourceName,
        contentText: document.contentText,
        contentHash: document.contentHash,
        retry,
      });
      if (imported.documentHash !== document.contentHash) return "HASH_MISMATCH" as const;
      if (imported.status === "PROCESSING") return "PROCESSING" as const;
      if (imported.status === "BUSY") return "SOURCE_BUSY" as const;
      if (imported.status === "ERROR") return "INDEXING_FAILED" as const;
      if (!imported.documentId) return "SOURCE_NOT_READY" as const;
      source = { documentId: imported.documentId, documentHash: imported.documentHash };
    } else {
      source = await client.findDocument(identity.organizationId, courseId, document.contentHash);
    }
    if (!source) return "SOURCE_NOT_READY" as const;
    if (source.documentHash !== document.contentHash) return "HASH_MISMATCH" as const;
    return repository.approve(identity, courseId, documentId, source, document.publishedSnapshotHash);
  };
}
