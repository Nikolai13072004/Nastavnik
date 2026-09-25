import type { Metadata } from "next";
import { connection } from "next/server";
import { MaxLaunch } from "./max-launch";

export const metadata: Metadata = {
  title: "Обучение в MAX — Prodigy",
  robots: { index: false, follow: false },
};

export default async function MaxPage() {
  await connection();
  return <MaxLaunch showLmsLinks={process.env.MAX_LMS_LINKS !== "disabled"}
    knowledgeCourseId={process.env.MAX_VEDOMO_ENABLED === "true" ? process.env.MAX_VEDOMO_COURSE_ID : undefined} />;
}
