import { verifyMaxLaunch } from "@/modules/max/server/verify-launch";

export const runtime = "nodejs";

export async function POST(request: Request) {
  return verifyMaxLaunch(request);
}
