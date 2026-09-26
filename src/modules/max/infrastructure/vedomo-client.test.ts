import assert from "node:assert/strict";
import test from "node:test";
import { createVedomoClient, VedomoClientError } from "./vedomo-client";

const token = "a".repeat(64);
const source = {
  document_id: "document-1", document_hash: "hash-1", title: "Регламент",
  section: "Раздел 1", page_start: 1, page_end: 1, snippet: "Ответственный подтверждает изменение.",
};

test("sends only the question with a tenant-bound service key", async () => {
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(input, "https://vedomo.example/api/integrations/prodigy/chat");
    assert.equal(init?.method, "POST");
    assert.equal((init?.headers as Record<string, string>)["Authorization"], `Bearer ${token}`);
    assert.equal((init?.headers as Record<string, string>)["X-Prodigy-Organization-ID"], "org-1");
    assert.equal((init?.headers as Record<string, string>)["X-Prodigy-Course-ID"], "course-1");
    assert.deepEqual(JSON.parse(init?.body as string), { question: "Кто подтверждает?" });
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    return Response.json({ answer: "Ответственный.", refused: false, sources: [source] });
  };
  const answer = await createVedomoClient("https://vedomo.example", token, fetcher).ask("org-1", "course-1", " Кто подтверждает? ");
  assert.equal(answer.sources[0].documentId, "document-1");
  assert.equal(answer.sources[0].documentHash, "hash-1");
});

test("looks up a ready document without exposing the service key", async () => {
  const documentHash = "b".repeat(64);
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(input, "https://vedomo.example/api/integrations/prodigy/documents/document-1");
    assert.equal(init?.method, "GET");
    assert.equal((init?.headers as Record<string, string>)["Authorization"], `Bearer ${token}`);
    assert.equal((init?.headers as Record<string, string>)["X-Prodigy-Course-ID"], "course-1");
    assert.equal(init?.redirect, "error");
    return Response.json({ document_id: "document-1", document_hash: documentHash, title: "policy.txt" });
  };
  const document = await createVedomoClient("https://vedomo.example", token, fetcher)
    .getDocument("org-1", "course-1", "document-1");
  assert.equal(document.documentHash, documentHash);
});

test("rejects missing, foreign, or malformed document metadata", async () => {
  const missing: typeof fetch = async () => new Response("not found", { status: 404 });
  await assert.rejects(
    () => createVedomoClient("https://vedomo.example", token, missing)
      .getDocument("org-1", "course-1", "document-1"),
    (error: unknown) => error instanceof VedomoClientError && error.code === "http" && error.status === 404,
  );
  const wrongId: typeof fetch = async () => Response.json({
    document_id: "foreign", document_hash: "b".repeat(64), title: "policy.txt",
  });
  await assert.rejects(
    () => createVedomoClient("https://vedomo.example", token, wrongId)
      .getDocument("org-1", "course-1", "document-1"),
    (error: unknown) => error instanceof VedomoClientError && error.code === "response",
  );
  await assert.rejects(
    () => createVedomoClient("https://vedomo.example", token, wrongId)
      .getDocument("org-1", "course-1", "../foreign"),
    (error: unknown) => error instanceof VedomoClientError && error.code === "configuration",
  );
});

test("refuses insecure configuration and header injection", async () => {
  assert.throws(() => createVedomoClient("http://vedomo.example", token), VedomoClientError);
  assert.throws(() => createVedomoClient("https://vedomo.example/path", token), VedomoClientError);
  assert.throws(() => createVedomoClient("https://vedomo.example", "bad\nkey"), VedomoClientError);
  await assert.rejects(
    () => createVedomoClient("https://vedomo.example", token).ask("org\nX: evil", "course-1", "Вопрос?"),
    VedomoClientError,
  );
  await assert.rejects(
    () => createVedomoClient("https://vedomo.example", token).ask("org-1", "course\nX: evil", "Вопрос?"),
    VedomoClientError,
  );
});

test("finds exact content with the scoped service credential", async () => {
  const hash = "a".repeat(64);
  const fetcher: typeof fetch = async (url, init) => {
    assert.equal(url, `https://vedomo.example/api/integrations/prodigy/documents/by-hash/${hash}`);
    assert.equal(init?.method, "GET");
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    assert.equal((init?.headers as Record<string, string>)["X-Prodigy-Organization-ID"], "org-1");
    assert.equal((init?.headers as Record<string, string>)["X-Prodigy-Course-ID"], "course-1");
    assert.equal((init?.headers as Record<string, string>).Authorization, `Bearer ${token}`);
    return Response.json({ document_id: "source-1", document_hash: hash, title: "policy.md" });
  };
  assert.deepEqual(await createVedomoClient("https://vedomo.example", token, fetcher)
    .findDocument("org-1", "course-1", hash), { documentId: "source-1", documentHash: hash, title: "policy.md" });
});

test("missing content is distinct from unavailable or ambiguous service", async () => {
  const hash = "a".repeat(64);
  const missing: typeof fetch = async () => new Response(null, { status: 404 });
  assert.equal(await createVedomoClient("https://vedomo.example", token, missing)
    .findDocument("org-1", "course-1", hash), null);
  for (const status of [401, 409, 503]) {
    const failed: typeof fetch = async () => new Response(`secret=${token}`, { status });
    await assert.rejects(() => createVedomoClient("https://vedomo.example", token, failed)
      .findDocument("org-1", "course-1", hash),
    (error: unknown) => error instanceof VedomoClientError && error.status === status && !error.message.includes(token));
  }
});

test("hash lookup rejects invalid request and mismatched response", async () => {
  const hash = "a".repeat(64);
  const fetcher: typeof fetch = async () => Response.json({
    document_id: "source-1", document_hash: "b".repeat(64), title: "policy.md",
  });
  const client = createVedomoClient("https://vedomo.example", token, fetcher);
  await assert.rejects(() => client.findDocument("org-1", "course-1", "bad"), VedomoClientError);
  await assert.rejects(() => client.findDocument("org-1", "course-1", hash),
    (error: unknown) => error instanceof VedomoClientError && error.code === "response");
});

test("does not expose an answer without a valid source", async () => {
  const fetcher: typeof fetch = async () => Response.json({ answer: "Выдуманный ответ", refused: false, sources: [] });
  await assert.rejects(() => createVedomoClient("https://vedomo.example", token, fetcher).ask("org-1", "course-1", "Вопрос?"),
    (error: unknown) => error instanceof VedomoClientError && error.code === "response");
});

test("upstream bodies and credentials never appear in errors", async () => {
  const fetcher: typeof fetch = async () => new Response(`secret=${token}`, { status: 503 });
  await assert.rejects(() => createVedomoClient("https://vedomo.example", token, fetcher).ask("org-1", "course-1", "Вопрос?"),
    (error: unknown) => error instanceof VedomoClientError && error.code === "http" &&
      !error.message.includes(token));
});
