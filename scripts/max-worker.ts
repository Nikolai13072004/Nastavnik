import { config } from "dotenv";
import { setTimeout as delay } from "node:timers/promises";
import prisma from "../src/lib/prisma";
import { deliverNextBotMessage } from "../src/modules/max/application/bot-delivery";
import { createMaxBotClient, MaxBotApiError } from "../src/modules/max/infrastructure/bot-client";
import { prismaBotDeliveryRepository } from "../src/modules/max/infrastructure/prisma-bot-delivery-repository";
import { prismaMaxLearnerRepository } from "../src/modules/max/infrastructure/prisma-max-learner-repository";
import { createLoadBotCourses } from "../src/modules/max/application/bot-course-menu";

config({ quiet: true });

async function main() {
  const token = process.env.MAX_BOT_TOKEN;
  const username = process.env.MAX_BOT_USERNAME;
  if (!token || !username) throw new Error("MAX worker is not configured");
  const client = createMaxBotClient(token);
  const profile = await client.getProfile();
  if (profile.username !== username) throw new Error("MAX bot does not match");
  const loadCourses = createLoadBotCourses(prismaMaxLearnerRepository);
  const sendMenu = async (userId: number, botUsername: string) => {
    const courses = await loadCourses(String(userId));
    try {
      if (courses === null) return await client.sendHelp(userId, botUsername);
      return await client.sendCourseMenu(
        userId, botUsername, courses, process.env.MAX_VEDOMO_COURSE_ID,
      );
    } catch (error) {
      if (error instanceof MaxBotApiError) {
        console.error(`MAX menu: ${error.code}, HTTP ${error.status ?? "unavailable"}`);
      }
      throw error;
    }
  };

  let stopping = false;
  process.once("SIGINT", () => { stopping = true; });
  process.once("SIGTERM", () => { stopping = true; });
  do {
    const result = await deliverNextBotMessage(
      prismaBotDeliveryRepository, username, client.sendWelcome, client.sendRevision, sendMenu,
    );
    console.log(`MAX delivery: ${result}`);
    if (result === "uncertain") throw new Error("Delivery requires operator review");
    if (process.argv[2] !== "loop" || stopping) break;
    await delay(1200);
  } while (!stopping);
}

main().catch(() => {
  // Prisma/upstream error details may contain connection strings or personal data.
  console.error("MAX worker stopped. Check configuration, database and delivery states; secrets are not printed.");
  process.exitCode = 1;
}).finally(() => prisma.$disconnect());
