import { spawn } from "node:child_process";

export const MAX_PDF_BYTES = 512 * 1024;
const MAX_PDF_PAGES = 10;
const MAX_TOOL_OUTPUT_BYTES = 64 * 1024;

export type PdfExtraction =
  | { text: string }
  | { error: "INVALID_PDF" | "PDF_TOO_LARGE" | "PDF_TOO_MANY_PAGES" | "PDF_NO_TEXT" };

function runPdfTool(command: "pdfinfo" | "pdftotext", args: string[], input: Buffer): Promise<string | null> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { stdio: ["pipe", "pipe", "ignore"] });
    const chunks: Buffer[] = [];
    let size = 0;
    let failed = false;
    const timeout = setTimeout(() => {
      failed = true;
      child.kill("SIGKILL");
    }, 5000);

    child.stdout.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_TOOL_OUTPUT_BYTES) {
        failed = true;
        child.kill("SIGKILL");
      } else {
        chunks.push(chunk);
      }
    });
    child.stdin.on("error", () => { failed = true; });
    child.on("error", () => { failed = true; });
    child.on("close", (code) => {
      clearTimeout(timeout);
      resolve(!failed && code === 0 ? Buffer.concat(chunks).toString("utf8") : null);
    });
    child.stdin.end(input);
  });
}

export async function extractPdfText(base64: string): Promise<PdfExtraction> {
  if (base64.length > Math.ceil(MAX_PDF_BYTES / 3) * 4 + 4) {
    return { error: "PDF_TOO_LARGE" };
  }
  if (!base64 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(base64)) {
    return { error: "INVALID_PDF" };
  }
  const bytes = Buffer.from(base64, "base64");
  if (bytes.length > MAX_PDF_BYTES) return { error: "PDF_TOO_LARGE" };
  if (bytes.subarray(0, 5).toString("ascii") !== "%PDF-") return { error: "INVALID_PDF" };

  const info = await runPdfTool("pdfinfo", ["-"], bytes);
  const pages = Number(info?.match(/^Pages:\s+(\d+)\s*$/m)?.[1]);
  if (!info || !Number.isInteger(pages) || pages < 1) return { error: "INVALID_PDF" };
  if (pages > MAX_PDF_PAGES) return { error: "PDF_TOO_MANY_PAGES" };

  const text = await runPdfTool("pdftotext", ["-enc", "UTF-8", "-nopgbrk", "-", "-"], bytes);
  if (text === null) return { error: "INVALID_PDF" };
  if (!text.trim()) return { error: "PDF_NO_TEXT" };
  return { text };
}
