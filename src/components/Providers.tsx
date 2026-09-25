"use client";

import { usePathname } from "next/navigation";
import { SessionProvider } from "next-auth/react";

export function Providers({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  // MAX uses its own short-lived learner session; the LMS endpoint is closed on the pilot gateway.
  if (pathname === "/max") return <>{children}</>;
  return <SessionProvider>{children}</SessionProvider>;
}
