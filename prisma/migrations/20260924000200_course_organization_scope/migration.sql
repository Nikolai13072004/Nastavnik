-- Nullable by design: historical courses have no trustworthy tenant marker.
-- Assign them only after reviewing their provenance; never infer from a
-- current owner's organization, which may have changed since publication.
ALTER TABLE "Course" ADD COLUMN "organizationId" TEXT;

CREATE INDEX "Course_organizationId_status_publishedAt_idx"
    ON "Course"("organizationId", "status", "publishedAt");

ALTER TABLE "Course"
    ADD CONSTRAINT "Course_organizationId_fkey"
    FOREIGN KEY ("organizationId") REFERENCES "Organization"("id")
    ON DELETE SET NULL ON UPDATE CASCADE;
