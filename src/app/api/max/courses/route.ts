import { listMaxCourses } from "@/modules/max/server/list-courses";

export const runtime = "nodejs";

export async function GET(request: Request) {
  return listMaxCourses(request);
}
