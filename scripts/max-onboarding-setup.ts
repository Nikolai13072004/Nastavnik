import { PrismaClient } from "@prisma/client";
import { provisionOnboardingCourse } from "../src/modules/max/infrastructure/provision-onboarding-course";

async function main() {
  if (!process.argv.includes("--create-pilot-course")) {
    throw new Error(
      "Use --create-pilot-course only against the isolated MAX pilot",
    );
  }
  const db = new PrismaClient();
  try {
    console.log(await provisionOnboardingCourse(db));
  } finally {
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error(
    "Onboarding setup failed; verify pilot scope and approved source. No secrets printed.",
  );
  process.exitCode = 1;
});
