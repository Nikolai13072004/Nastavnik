import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { extractPdfText } from "@/modules/max/server/extract-pdf-text";
import {
  getMaxCourseDocuments,
  getMaxManagerDocuments,
  manageMaxCourseDocuments,
} from "@/modules/max/server/course-documents";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

function authenticate(request: Request) {
  const botToken = process.env.MAX_BOT_TOKEN;
  const authorization = request.headers.get("authorization") ?? "";
  return botToken && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
}

async function readBody(request: Request, maxBytes = 48 * 1024): Promise<unknown> {
  if (!request.body) throw new SyntaxError("Missing body");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        throw new RangeError("Request too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function errorResponse(error: string) {
  const status = error === "SESSION_REVOKED" ? 401 : error === "FORBIDDEN" ? 403 : error === "NOT_FOUND" ? 404 : 503;
  return Response.json({ error }, { status, headers });
}

export async function GET(request: Request) {
  const identity = authenticate(request);
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  const query = new URL(request.url).searchParams;
  const courseId = query.get("courseId") ?? "";
  const documentId = query.get("documentId") ?? undefined;
  if (!courseId || courseId.length > 128 || (documentId && documentId.length > 128)) {
    return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
  }
  try {
    const result = query.get("scope") === "manager"
      ? await getMaxManagerDocuments(identity, courseId, documentId)
      : await getMaxCourseDocuments(identity, courseId, documentId);
    return "error" in result && result.error
      ? errorResponse(result.error)
      : Response.json(result, { headers });
  } catch {
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}

export async function POST(request: Request) {
  const identity = authenticate(request);
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return Response.json({ error: "JSON_REQUIRED" }, { status: 415, headers });
  }
  try {
    const body = await readBody(request, 720 * 1024);
    if (!body || typeof body !== "object" ||
        !("courseId" in body) || !("title" in body) || !("sourceName" in body) ||
        typeof body.courseId !== "string" || typeof body.title !== "string" ||
        typeof body.sourceName !== "string") {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }
    const supersedesId = "supersedesId" in body ? body.supersedesId : undefined;
    const changeSummary = "changeSummary" in body ? body.changeSummary : undefined;
    const checkQuestion = "checkQuestion" in body ? body.checkQuestion : undefined;
    const checkOptions = "checkOptions" in body ? body.checkOptions : undefined;
    const checkCorrectIndex = "checkCorrectIndex" in body ? body.checkCorrectIndex : undefined;
    if ((supersedesId !== undefined && typeof supersedesId !== "string") ||
        (changeSummary !== undefined && typeof changeSummary !== "string") ||
        (checkQuestion !== undefined && typeof checkQuestion !== "string") ||
        (checkOptions !== undefined && (!Array.isArray(checkOptions) ||
          !checkOptions.every((option) => typeof option === "string"))) ||
        (checkCorrectIndex !== undefined && !Number.isInteger(checkCorrectIndex))) {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }
    let contentText: string;
    if (/\.pdf$/i.test(body.sourceName)) {
      if (!("fileBase64" in body) || typeof body.fileBase64 !== "string" || "contentText" in body) {
        return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
      }
      if (body.courseId.length > 128 || !(await getMaxManagerDocuments(identity, body.courseId)).documents) {
        return Response.json({ error: "NOT_FOUND" }, { status: 404, headers });
      }
      const extracted = await extractPdfText(body.fileBase64);
      if ("error" in extracted) {
        const status = extracted.error === "PDF_TOO_LARGE" ? 413 : 400;
        return Response.json({ error: extracted.error }, { status, headers });
      }
      contentText = extracted.text;
    } else {
      if (!("contentText" in body) || typeof body.contentText !== "string" || "fileBase64" in body) {
        return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
      }
      contentText = body.contentText;
    }
    const status = await manageMaxCourseDocuments.upload(identity, {
      courseId: body.courseId,
      title: body.title,
      sourceName: body.sourceName,
      contentText,
      supersedesId: supersedesId as string | undefined,
      changeSummary: changeSummary as string | undefined,
      checkQuestion: checkQuestion as string | undefined,
      checkOptions: checkOptions as string[] | undefined,
      checkCorrectIndex: checkCorrectIndex as number | undefined,
    });
    if (status === "INVALID_INPUT") return Response.json({ status }, { status: 400, headers });
    if (status === "FORBIDDEN") return Response.json({ status }, { status: 403, headers });
    if (status === "NOT_FOUND") return Response.json({ status }, { status: 404, headers });
    if (status === "LIMIT_REACHED") return Response.json({ status }, { status: 409, headers });
    if (status === "CONFLICT") return Response.json({ status }, { status: 409, headers });
    return Response.json({ status }, { status: 201, headers });
  } catch (error) {
    const status = error instanceof RangeError ? 413 : error instanceof SyntaxError ? 400 : 503;
    return Response.json({ error: status === 413 ? "REQUEST_TOO_LARGE" : status === 400 ? "INVALID_REQUEST" : "TEMPORARILY_UNAVAILABLE" }, { status, headers });
  }
}

export async function PUT(request: Request) {
  const identity = authenticate(request);
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return Response.json({ error: "JSON_REQUIRED" }, { status: 415, headers });
  }
  try {
    const body = await readBody(request);
    if (!body || typeof body !== "object" || !("courseId" in body) || !("documentId" in body) ||
        !("publish" in body) || typeof body.courseId !== "string" ||
        typeof body.documentId !== "string" || typeof body.publish !== "boolean") {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }
    const expectedRecipientIds = "expectedRecipientIds" in body ? body.expectedRecipientIds : undefined;
    if (expectedRecipientIds !== undefined && (!Array.isArray(expectedRecipientIds) ||
        !expectedRecipientIds.every((id) => typeof id === "string"))) {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }
    const status = await manageMaxCourseDocuments.setPublished(
      identity, body.courseId, body.documentId, body.publish, expectedRecipientIds,
    );
    if (status === "INVALID_INPUT") return Response.json({ status }, { status: 400, headers });
    if (status === "FORBIDDEN") return Response.json({ status }, { status: 403, headers });
    if (status === "NOT_FOUND") return Response.json({ status }, { status: 404, headers });
    if (status === "AUDIENCE_CHANGED" || status === "AUDIENCE_TOO_LARGE" || status === "CONFLICT") {
      return Response.json({ status }, { status: 409, headers });
    }
    return Response.json({ status }, { headers });
  } catch (error) {
    const status = error instanceof RangeError ? 413 : error instanceof SyntaxError ? 400 : 503;
    return Response.json({ error: status === 413 ? "REQUEST_TOO_LARGE" : status === 400 ? "INVALID_REQUEST" : "TEMPORARILY_UNAVAILABLE" }, { status, headers });
  }
}
