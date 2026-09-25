ALTER TABLE "MaxKnowledgeDocument"
    ADD COLUMN "courseDocumentId" TEXT;

CREATE UNIQUE INDEX "max_knowledge_doc_course_source_key"
    ON "MaxKnowledgeDocument"("organizationId", "courseId", "courseDocumentId");

ALTER TABLE "MaxKnowledgeDocument"
    ADD CONSTRAINT "MaxKnowledgeDocument_courseDocumentId_fkey"
    FOREIGN KEY ("courseDocumentId") REFERENCES "MaxCourseDocument"("id") ON DELETE SET NULL ON UPDATE CASCADE;
