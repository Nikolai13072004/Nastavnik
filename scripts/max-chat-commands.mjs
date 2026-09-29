import { pathToFileURL } from "node:url";

const requiredCommands = [
  { name: "start", description: "Открыть меню обучения" },
  { name: "menu", description: "Выбрать курс и действие" },
  { name: "courses", description: "Мои назначенные курсы" },
  { name: "help", description: "Как пользоваться ботом" },
];

export function mergeChatCommands(current) {
  if (current == null) current = [];
  if (!Array.isArray(current) || current.some((command) =>
    !command || typeof command.name !== "string" || typeof command.description !== "string")) {
    throw new Error("Invalid existing commands");
  }
  const names = new Set(requiredCommands.map((command) => command.name));
  const merged = [...requiredCommands, ...current.filter((command) => !names.has(command.name))];
  if (merged.length > 32) throw new Error("Command limit exceeded; existing commands preserved");
  return merged;
}

async function main() {
  const token = process.env.MAX_BOT_TOKEN;
  const username = process.env.MAX_BOT_USERNAME;
  if (!token || !username || process.env.MAX_CHAT_ENABLED !== "true") {
    throw new Error("Enabled chat and explicit bot configuration required");
  }

  async function request(path, body) {
    const response = await fetch(`https://platform-api2.max.ru${path}`, {
      method: body ? "PATCH" : "GET",
      headers: { Authorization: token, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(10_000),
      redirect: "error",
      cache: "no-store",
    });
    if (!response.ok) throw new Error("MAX API unavailable");
    return response.json();
  }

  const profile = await request("/me");
  if (profile.username !== username || profile.is_bot !== true) throw new Error("Bot mismatch");
  const commands = mergeChatCommands(profile.commands);
  if (JSON.stringify(profile.commands) === JSON.stringify(commands)) {
    console.log("MAX command hints already configured; no change.");
    return;
  }
  if (!process.argv.includes("--apply")) {
    console.log("MAX command hints need updating; use --apply. Other commands will be preserved.");
    return;
  }
  // Do not retry a mutation after an uncertain network response.
  await request("/me/commands", { commands });
  const verified = await request("/me");
  if (JSON.stringify(verified.commands) !== JSON.stringify(commands)) {
    throw new Error("Command hints not verified");
  }
  console.log("MAX command hints verified: start, menu, courses, help. Other commands preserved.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => {
    console.error("MAX command setup failed. Check bot configuration and TLS; secrets are not printed.");
    process.exitCode = 1;
  });
}
