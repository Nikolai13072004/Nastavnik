import "server-only";

import { createListMaxCourses } from "../application/list-courses";
import { prismaMaxLearnerRepository } from "../infrastructure/prisma-max-learner-repository";
import { handleMaxCourses } from "../infrastructure/courses-handler";

const listCourses = createListMaxCourses(prismaMaxLearnerRepository);

export function listMaxCourses(request: Request) {
  return handleMaxCourses(request, process.env.MAX_BOT_TOKEN, listCourses);
}
