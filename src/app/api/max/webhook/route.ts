import { receiveMaxEvent } from "@/modules/max/server/receive-event";

export const runtime = "nodejs";

export async function POST(request: Request) {
  return receiveMaxEvent(request);
}
