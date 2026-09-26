import { handleKnowledgeConnection } from "@/modules/max/infrastructure/knowledge-connection-handler";
import { connectMaxKnowledgeDocument } from "@/modules/max/server/connect-knowledge-document";

export const runtime = "nodejs";

export async function POST(request: Request) {
  return handleKnowledgeConnection(request, process.env.MAX_BOT_TOKEN,
    process.env.MAX_VEDOMO_ENABLED === "true", connectMaxKnowledgeDocument);
}
