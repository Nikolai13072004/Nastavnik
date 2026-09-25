import { auth } from "@/auth";
import { MaxLinkError } from "@/modules/max/application/link-account";
import { maxAccountLinking } from "@/modules/max/server/account-linking";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const headers = { "Cache-Control": "no-store" };
  // Cookie-authenticated mutation: reject cross-origin and origin-less requests.
  if (request.headers.get("origin") !== new URL(request.url).origin) {
    return Response.json({ error: "FORBIDDEN" }, { status: 403, headers });
  }
  const session = await auth();
  if (!session?.user) return Response.json({ error: "UNAUTHORIZED" }, { status: 401, headers });
  try {
    // No target user/org/role is accepted from the browser. Only the session owner.
    const invite = await maxAccountLinking.issue(session.user.id);
    return Response.json(invite, { headers });
  } catch (error) {
    if (error instanceof MaxLinkError) {
      return Response.json({ error: error.code }, { status: 409, headers });
    }
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}
