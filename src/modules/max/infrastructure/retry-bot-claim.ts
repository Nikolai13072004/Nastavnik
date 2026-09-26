import { setTimeout as delay } from "node:timers/promises";
import type { BotDelivery } from "../application/bot-delivery";

const transientCodes = new Set(["P1001", "P1002", "P1008", "P1017", "P2024", "P2028", "P2034"]);

// Retry only queue acquisition, never the sender or delivery completion.
// If a claim committed but its response was lost, the SENDING guard still blocks resending.
export async function retryBotClaim(
  claim: () => Promise<BotDelivery | null>,
  wait: (milliseconds: number) => Promise<void> = delay,
  report: (code: string, attempt: number) => void = (code, attempt) => {
    console.warn(`MAX queue temporarily unavailable: ${code}, retry=${attempt}`);
  },
): Promise<BotDelivery | null> {
  for (let attempt = 1; ; attempt++) {
    try {
      return await claim();
    } catch (error) {
      const code = error && typeof error === "object" && "code" in error
        ? error.code
        : undefined;
      if (typeof code !== "string" || !transientCodes.has(code) || attempt >= 3) throw error;
      report(code, attempt);
      await wait(attempt * 500);
    }
  }
}
