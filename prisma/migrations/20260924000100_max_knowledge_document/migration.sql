CREATE TABLE "MaxKnowledgeDocument" (
    "id" TEXT NOT NULL,
    "organizationId" TEXT NOT NULL,
    "courseId" TEXT NOT NULL,
    "publishedSnapshotHash" TEXT NOT NULL,
    "vedomoDocumentId" TEXT NOT NULL,
    "vedomoDocumentHash" TEXT NOT NULL,
    "approvedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "revokedAt" TIMESTAMP(3),

    CONSTRAINT "MaxKnowledgeDocument_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "max_knowledge_doc_scope_key"
    ON "MaxKnowledgeDocument"("organizationId", "courseId", "vedomoDocumentId");

CREATE INDEX "max_knowledge_doc_current_idx"
    ON "MaxKnowledgeDocument"("organizationId", "courseId", "publishedSnapshotHash", "revokedAt");

ALTER TABLE "MaxKnowledgeDocument"
    ADD CONSTRAINT "MaxKnowledgeDocument_organizationId_fkey"
    FOREIGN KEY ("organizationId") REFERENCES "Organization"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "MaxKnowledgeDocument"
    ADD CONSTRAINT "MaxKnowledgeDocument_courseId_fkey"
    FOREIGN KEY ("courseId") REFERENCES "Course"("id") ON DELETE CASCADE ON UPDATE CASCADE;
