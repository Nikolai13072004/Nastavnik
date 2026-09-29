import type { Metadata } from "next";
import { connection } from "next/server";
import { MaxLaunch } from "./max-launch";

export const metadata: Metadata = {
  title: "Наставник - обучение в MAX",
  icons: { icon: "/branding/nastavnik-avatar.png" },
  robots: { index: false, follow: false },
};

export default async function MaxPage({ searchParams }: {
  searchParams: Promise<{ preview?: string | string[] }>;
}) {
  await connection();
  const params = await searchParams;
  const designPreview = process.env.MAX_DESIGN_PREVIEW === "true" && params.preview === "1";
  return <MaxLaunch showLmsLinks={process.env.MAX_LMS_LINKS === "enabled"}
    designPreview={designPreview}
    botUsername={process.env.MAX_BOT_USERNAME}
    knowledgeCourseId={process.env.MAX_VEDOMO_ENABLED === "true" ? process.env.MAX_VEDOMO_COURSE_ID : undefined} />;
}
