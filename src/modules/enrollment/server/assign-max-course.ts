import "server-only";

import { createAssignMaxCourse } from "../application/assign-max-course";
import { prismaMaxCourseAssignmentRepository } from "../infrastructure/prisma-max-course-assignment-repository";

export const assignMaxCourse = createAssignMaxCourse(prismaMaxCourseAssignmentRepository);
