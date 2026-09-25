import { config } from "dotenv";
import { createMaxBotClient, MaxBotApiError } from "../src/modules/max/infrastructure/bot-client";

config({ quiet: true });

async function main() {
  const token = process.env.MAX_BOT_TOKEN;
  const expectedUsername = process.env.MAX_BOT_USERNAME;
  if (!token || !expectedUsername) {
    console.error("MAX: укажите MAX_BOT_TOKEN и MAX_BOT_USERNAME в локальном .env. Секрет в чат не присылайте.");
    process.exitCode = 1;
    return;
  }

  const profile = await createMaxBotClient(token).getProfile();
  if (profile.username !== expectedUsername) {
    console.error("MAX: токен принадлежит другому боту. Подключение остановлено.");
    process.exitCode = 1;
    return;
  }
  console.log("MAX: токен действителен, бот совпадает с настройкой. Изменений в MAX не сделано.");
  console.log("Это только проверка API: HTTPS, Mini App URL и запуск внутри MAX нужно проверить отдельно.");
}

main().catch((error: unknown) => {
  console.error(error instanceof MaxBotApiError ? error.message : "MAX: проверка не выполнена.");
  process.exitCode = 1;
});
