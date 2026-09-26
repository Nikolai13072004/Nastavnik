import { config } from "dotenv";
import { setTimeout as delay } from "node:timers/promises";
import prisma from "../src/lib/prisma";
import { deliverNextBotMessage } from "../src/modules/max/application/bot-delivery";
import {
  createMaxBotClient,
  MaxBotApiError,
} from "../src/modules/max/infrastructure/bot-client";
import { prismaBotDeliveryRepository } from "../src/modules/max/infrastructure/prisma-bot-delivery-repository";
import { prismaMaxLearnerRepository } from "../src/modules/max/infrastructure/prisma-max-learner-repository";
import { createLoadBotCourses } from "../src/modules/max/application/bot-course-menu";
import { randomBytes } from "node:crypto";
import { createChatFlow } from "../src/modules/max/application/chat-flow";
import { createListMaxCourses } from "../src/modules/max/application/list-courses";
import { createChatInputCodec } from "../src/modules/max/infrastructure/chat-input-codec";
import { createChatLearningClient } from "../src/modules/max/infrastructure/chat-learning-client";
import { createPrismaChatSessions } from "../src/modules/max/infrastructure/prisma-chat-session-repository";

config({ quiet: true });

async function main() {
  const token = process.env.MAX_BOT_TOKEN;
  const username = process.env.MAX_BOT_USERNAME;
  if (!token || !username) throw new Error("MAX worker is not configured");
  const client = createMaxBotClient(token);
  const profile = await client.getProfile();
  if (profile.username !== username) throw new Error("MAX bot does not match");
  const loadCourses = createLoadBotCourses(prismaMaxLearnerRepository);
  const chatEnabled = process.env.MAX_CHAT_ENABLED === "true";
  const chatFlow = chatEnabled
    ? createChatFlow({
        access: {
          identity: prismaMaxLearnerRepository.findIdentity,
          courses: createListMaxCourses(prismaMaxLearnerRepository),
        },
        sessions: createPrismaChatSessions(username),
        learning: createChatLearningClient(
          token,
          process.env.MAX_CHAT_INTERNAL_ORIGIN ?? "http://web:3000",
        ),
        botUsername: username,
        version: () => randomBytes(12).toString("base64url"),
      })
    : null;
  const inputCodec = createChatInputCodec(token);
  const sendMenu = async (userId: number, botUsername: string) => {
    if (chatFlow) {
      const reply = await chatFlow(String(userId), {
        type: "text",
        text: "курсы",
        messageId: "menu",
      });
      return client.sendChat(userId, botUsername, reply);
    }
    const courses = await loadCourses(String(userId));
    try {
      if (courses === null) return await client.sendHelp(userId, botUsername);
      return await client.sendCourseMenu(
        userId,
        botUsername,
        courses,
        process.env.MAX_VEDOMO_COURSE_ID,
      );
    } catch (error) {
      if (error instanceof MaxBotApiError) {
        console.error(
          `MAX menu: ${error.code}, HTTP ${error.status ?? "unavailable"}`,
        );
      }
      throw error;
    }
  };

  let stopping = false;
  let lastIdleLog = 0;
  process.once("SIGINT", () => {
    stopping = true;
  });
  process.once("SIGTERM", () => {
    stopping = true;
  });
  do {
    const processingStarted = performance.now();
    const result = await deliverNextBotMessage(
      prismaBotDeliveryRepository,
      username,
      chatEnabled ? sendMenu : client.sendWelcome,
      client.sendRevision,
      sendMenu,
      async (job) => {
        if (!chatFlow || !job.chatInputCiphertext)
          throw new Error("Chat sender is not configured");
        const input = inputCodec.open(job.chatInputCiphertext, job.eventKey);
        const reply = await chatFlow(job.maxUserId, input);
        return input.type === "callback"
          ? client.answerCallback(input.callbackId, input.messageId, reply)
          : client.sendChat(Number(job.maxUserId), username, reply);
      },
    );
    if (result !== "idle") {
      console.log(
        `MAX delivery: ${result}, processing=${Math.round(performance.now() - processingStarted)}ms`,
      );
    } else if (Date.now() - lastIdleLog >= 60_000) {
      console.log("MAX delivery: idle");
      lastIdleLog = Date.now();
    }
    if (result === "uncertain")
      throw new Error("Delivery requires operator review");
    if (process.argv[2] !== "loop" || stopping) break;
    await delay(result === "idle" ? 250 : 50);
  } while (!stopping);
}

main()
  .catch(() => {
    // Prisma/upstream error details may contain connection strings or personal data.
    console.error(
      "MAX worker stopped. Check configuration, database and delivery states; secrets are not printed.",
    );
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
