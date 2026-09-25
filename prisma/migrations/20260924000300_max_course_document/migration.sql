CREATE TABLE "MaxCourseDocument" (
    "id" TEXT NOT NULL,
    "organizationId" TEXT NOT NULL,
    "courseId" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "sourceName" TEXT NOT NULL,
    "contentText" TEXT NOT NULL,
    "contentHash" TEXT NOT NULL,
    "uploadedById" TEXT NOT NULL,
    "approvedById" TEXT,
    "approvedAt" TIMESTAMP(3),
    "revokedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "MaxCourseDocument_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "max_course_doc_scope_idx"
    ON "MaxCourseDocument"("organizationId", "courseId", "approvedAt", "revokedAt");

ALTER TABLE "MaxCourseDocument"
    ADD CONSTRAINT "MaxCourseDocument_organizationId_fkey"
    FOREIGN KEY ("organizationId") REFERENCES "Organization"("id") ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "MaxCourseDocument"
    ADD CONSTRAINT "MaxCourseDocument_courseId_fkey"
    FOREIGN KEY ("courseId") REFERENCES "Course"("id") ON DELETE CASCADE ON UPDATE CASCADE;
