const origin = "https://platform-api2.max.ru";

async function main() {
  const token = process.env.MAX_BOT_TOKEN;
  const username = process.env.MAX_BOT_USERNAME;
  const secret = process.env.MAX_WEBHOOK_SECRET;
  const endpoint = new URL("/api/max/webhook", process.env.APP_BASE_URL);
  if (!token || !username || !secret || secret === token
    || !/^[a-zA-Z0-9_-]{32,256}$/.test(secret)
    || endpoint.protocol !== "https:" || endpoint.username || endpoint.password
    || endpoint.port) throw new Error("Invalid configuration");

  async function request(path, body) {
    const response = await fetch(`${origin}${path}`, {
      method: body ? "POST" : "GET",
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
  const subscriptions = (await request("/subscriptions")).subscriptions;
  if (!Array.isArray(subscriptions)) throw new Error("Invalid subscriptions");
  const own = subscriptions.filter((subscription) => subscription.url === endpoint.href);
  if (own.length !== 1) throw new Error("Expected exactly one existing pilot subscription");
  const current = own[0].update_types;
  const required = process.env.MAX_CHAT_ENABLED === "true"
    ? ["message_created", "message_callback"] : ["message_created"];
  if (!Array.isArray(current) || current.length === 0 || required.every((type) => current.includes(type))) {
    console.log("MAX subscription: required events already enabled; no change.");
    return;
  }
  if (!current.every((type) => typeof type === "string")) throw new Error("Invalid event types");
  if (!process.argv.includes("--apply")) {
    console.log("MAX subscription: required events missing; rerun with --apply after deploying web and worker.");
    return;
  }
  const result = await request("/subscriptions", {
    url: endpoint.href, update_types: [...new Set([...current, ...required])], secret,
  });
  if (result.success !== true) throw new Error("Subscription change not confirmed");
  const updated = (await request("/subscriptions")).subscriptions;
  if (!Array.isArray(updated) || !updated.some((subscription) =>
    subscription.url === endpoint.href && Array.isArray(subscription.update_types)
    && required.every((type) => subscription.update_types.includes(type)))) throw new Error("Subscription not verified");
  console.log("MAX subscription: required events enabled; existing events preserved.");
}

main().catch(() => {
  console.error("MAX subscription check failed. Check bot, URL, TLS and configuration; secrets are not printed.");
  process.exitCode = 1;
});
