import { handleMaxKnowledgeRequest } from "@/modules/max/infrastructure/knowledge-handler";
import { askMaxKnowledge } from "@/modules/max/server/ask-knowledge";

export const runtime = "nodejs";

export async function POST(request: Request) {
  return handleMaxKnowledgeRequest(
    request,
    process.env.MAX_BOT_TOKEN,
    process.env.MAX_VEDOMO_ENABLED === "true",
    askMaxKnowledge,
  );
}
