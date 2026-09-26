// Next.js 16 uses proxy.ts instead of middleware.ts. This still wires
// NextAuth's authorized callback into the route guard layer.
export { auth as proxy } from "@/auth";

export const config = {
  // MAX courses use a separate learner-only bearer session, never NextAuth roles.
  // Invitation issuance remains behind the ordinary LMS session guard.
  // Webhook uses its own server-to-server secret, not a browser session.
  matcher: ["/((?!max/?$|api/max/(?:identity|courses|course|course-search|knowledge|quiz|manager-report|employees|documents(?:/knowledge)?|document-training|webhook)/?$|api/auth|api/health|api/upload|_next/static|_next/image|favicon.ico|uploads|branding).*)"],
};
