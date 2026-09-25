ALTER TABLE "MaxCourseDocument"
    ADD COLUMN "supersedesId" TEXT,
    ADD COLUMN "versionNumber" INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN "changeSummary" TEXT,
    ADD COLUMN "checkQuestion" TEXT,
    ADD COLUMN "checkOptionsJson" TEXT,
    ADD COLUMN "checkCorrectIndex" INTEGER;

CREATE UNIQUE INDEX "MaxCourseDocument_supersedesId_key"
    ON "MaxCourseDocument"("supersedesId");

CREATE TABLE "MaxDocumentTraining" (
    "id" TEXT NOT NULL,
    "organizationId" TEXT NOT NULL,
    "courseId" TEXT NOT NULL,
    "documentId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "assignedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "viewedAt" TIMESTAMP(3),
    "passedAt" TIMESTAMP(3),
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "lastAnsweredAt" TIMESTAMP(3),

    CONSTRAINT "MaxDocumentTraining_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "MaxDocumentTraining_documentId_userId_key"
    ON "MaxDocumentTraining"("documentId", "userId");
CREATE INDEX "MaxDocumentTraining_organizationId_courseId_documentId_idx"
    ON "MaxDocumentTraining"("organizationId", "courseId", "documentId");

ALTER TABLE "MaxDocumentTraining" ADD CONSTRAINT "MaxDocumentTraining_organizationId_fkey"
    FOREIGN KEY ("organizationId") REFERENCES "Organization"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxDocumentTraining" ADD CONSTRAINT "MaxDocumentTraining_courseId_fkey"
    FOREIGN KEY ("courseId") REFERENCES "Course"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxDocumentTraining" ADD CONSTRAINT "MaxDocumentTraining_documentId_fkey"
    FOREIGN KEY ("documentId") REFERENCES "MaxCourseDocument"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxDocumentTraining" ADD CONSTRAINT "MaxDocumentTraining_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
