import assert from "node:assert/strict";
import { test } from "node:test";
import { createMaxBotClient, MaxBotApiError } from "./bot-client";

const profile = { user_id: 123, username: "example_bot", first_name: "Example", is_bot: true };

test("callback refreshes the existing message together with a notification", async () => {
  const bodies: unknown[] = [];
  const client = createMaxBotClient("test-secret", async (url, init) => {
    assert.equal(url, "https://platform-api2.max.ru/answers?callback_id=cb%2F1");
    bodies.push(JSON.parse(String(init?.body)));
    return Response.json({ success: true });
  });
  assert.equal(await client.answerCallback("cb/1", "mid", { text: "Вопрос 2", buttons: [] }), "mid");
  const buttons = [[{
    type: "callback" as const,
    text: "Прогресс",
    payload: "chat:1234567890123456:progress",
  }]];
  await client.answerCallback("cb/1", "mid", {
    notification: "Меню обновлено.",
    text: "Выберите действие.",
    buttons,
  });
  assert.deepEqual(bodies[1], {
    notification: "Меню обновлено.",
    message: {
      text: "Выберите действие.",
      attachments: [{ type: "inline_keyboard", payload: { buttons } }],
    },
  });
  await client.answerCallback("cb/1", "mid", { notification: "Действие недоступно." });
  assert.deepEqual(bodies[2], { notification: "Действие недоступно." });
});

test("unsuccessful callback response is not assumed delivered or retried", async () => {
  let calls = 0;
  const client = createMaxBotClient("test-secret", async () => { calls++; return Response.json({ success: false }); });
  await assert.rejects(client.answerCallback("cb", "mid", { text: "Ответ" }), MaxBotApiError);
  assert.equal(calls, 1);
});

test("profile check uses only official HTTPS origin, header auth, deadline and no redirects/cache", async () => {
  const client = createMaxBotClient("test-secret", async (url, init) => {
    assert.equal(url, "https://platform-api2.max.ru/me");
    assert.equal(init?.method, "GET");
    assert.equal(new Headers(init?.headers).get("Authorization"), "test-secret");
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    assert.ok(init?.signal instanceof AbortSignal);
    return Response.json(profile);
  });
  assert.deepEqual(await client.getProfile(), { userId: 123, username: "example_bot", firstName: "Example" });
});

test("welcome opens the registered bot Mini App, never sends an invitation code or grants LMS access", async () => {
  const client = createMaxBotClient("test-secret", async (url, init) => {
    assert.equal(url, "https://platform-api2.max.ru/messages?user_id=456");
    assert.equal(init?.method, "POST");
    const body = JSON.parse(String(init?.body));
    assert.deepEqual(body.attachments, [{ type: "inline_keyboard", payload: {
      buttons: [[{ type: "open_app", text: "Открыть обучение", web_app: "example_bot" }]],
    } }]);
    assert.match(body.text, /не отправляйте его в чат/);
    return Response.json({ message: { body: { mid: "message-id" } } });
  });
  assert.equal(await client.sendWelcome(456, "example_bot"), "message-id");
});

test("revision notice opens the app without exposing document text", async () => {
  const client = createMaxBotClient("test-secret", async (url, init) => {
    assert.equal(url, "https://platform-api2.max.ru/messages?user_id=456");
    const body = JSON.parse(String(init?.body));
    assert.match(body.text, /обновился рабочий документ/);
    assert.deepEqual(body.attachments, [{ type: "inline_keyboard", payload: {
      buttons: [[{ type: "open_app", text: "Открыть обучение", web_app: "example_bot" }]],
    } }]);
    return Response.json({ message: { body: { mid: "revision-mid" } } });
  });
  assert.equal(await client.sendRevision(456, "example_bot"), "revision-mid");
});

test("generic help replies privately with honest capabilities and no user-supplied text", async () => {
  const client = createMaxBotClient("test-secret", async (url, init) => {
    assert.equal(url, "https://platform-api2.max.ru/messages?user_id=456");
    const body = JSON.parse(String(init?.body));
    assert.match(body.text, /Вопросы и тесты прямо в чате добавим отдельно/);
    assert.match(body.text, /Не отправляйте в чат коды/);
    assert.equal(body.attachments[0].payload.buttons[0][0].type, "open_app");
    return Response.json({ message: { body: { mid: "help-mid" } } });
  });
  assert.equal(await client.sendHelp(456, "example_bot"), "help-mid");
});

test("course menu sends assigned titles with short, course-specific app buttons", async () => {
  const client = createMaxBotClient("test-secret", async (url, init) => {
    assert.equal(url, "https://platform-api2.max.ru/messages?user_id=456");
    const body = JSON.parse(String(init?.body));
    assert.match(body.text, /1\. Onboarding/);
    assert.equal(body.format, undefined);
    assert.deepEqual(body.attachments[0].payload.buttons[0], [{
      type: "open_app", text: "Курс 1", web_app: "example_bot", payload: "course_onboarding",
    }]);
    return Response.json({ message: { body: { mid: "menu-mid" } } });
  });
  assert.equal(await client.sendCourseMenu(456, "example_bot", [{ id: "onboarding", title: "Onboarding", expiresAt: null }]), "menu-mid");
});

test("HTTP failures, rate limits and transport failures do not retry or leak secrets", async () => {
  for (const status of [401, 429, 500]) {
    let calls = 0;
    const client = createMaxBotClient("test-secret", async () => {
      calls++;
      return new Response("private upstream details test-secret", { status });
    });
    await assert.rejects(client.sendWelcome(456, "example_bot"), (error: unknown) => {
      assert.ok(error instanceof MaxBotApiError);
      assert.equal(error.status, status);
      assert.ok(!String(error).includes("test-secret"));
      return true;
    });
    assert.equal(calls, 1);
  }
  const client = createMaxBotClient("test-secret", async () => { throw new Error("test-secret"); });
  await assert.rejects(client.getProfile(), { message: "MAX API: transport" });
});

test("malformed, oversized, non-bot and unsafe numeric responses fail closed", async () => {
  for (const data of [{}, { ...profile, is_bot: false }, { ...profile, user_id: 2 ** 53 },
    { ...profile, username: "https://other.example" }]) {
    const client = createMaxBotClient("test-secret", async () => Response.json(data));
    await assert.rejects(client.getProfile(), { message: "MAX API: response" });
  }
  for (const body of ["not-json", "x".repeat(65537)]) {
    const client = createMaxBotClient("test-secret", async () => new Response(body));
    await assert.rejects(client.getProfile(), { message: "MAX API: response" });
  }
  const client = createMaxBotClient("test-secret", async () => Response.json({ success: true }));
  await assert.rejects(client.sendWelcome(456, "example_bot"), { message: "MAX API: response" });
});

test("invalid configuration cannot perform an outbound request", async () => {
  for (const token of ["", " token", "token\r\nheader"]) {
    assert.throws(() => createMaxBotClient(token), MaxBotApiError);
  }
  const client = createMaxBotClient("test-secret", async () => assert.fail("must not send"));
  for (const id of [0, -1, 1.1, 2 ** 53, NaN]) {
    await assert.rejects(client.sendWelcome(id, "example_bot"), MaxBotApiError);
  }
  await assert.rejects(client.sendWelcome(123, "https://other.example"), MaxBotApiError);
});
