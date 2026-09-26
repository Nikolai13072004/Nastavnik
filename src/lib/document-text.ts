export type DocumentBlock =
  | { type: "heading" | "paragraph" | "code"; text: string }
  | { type: "list"; ordered: boolean; items: string[] };

// A small text-only Markdown subset. No HTML or document-supplied URLs are executed.
export function readableInlineText(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^\n)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^\n)]*\)/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(
      /\*\*([^\n]+?)\*\*|__([^\n]+?)__/g,
      (_, bold, alternate) => bold ?? alternate,
    )
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|[.,!?;:]|$)/g, "$1$2");
}

export function documentBlocks(text: string): DocumentBlock[] {
  const blocks: DocumentBlock[] = [];
  let code: string[] | null = null;
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*```/.test(raw)) {
      if (code) {
        blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else {
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(raw);
      continue;
    }
    const line = raw.trim();
    if (!line || /^(?:-{3,}|\*{3,}|_{3,})$/.test(line)) {
      // Preserve paragraph boundaries without retaining Markdown separators.
      if (blocks.at(-1)?.type === "paragraph")
        blocks.push({ type: "paragraph", text: "" });
      continue;
    }
    const heading = /^#{1,6}\s+(.+?)(?:\s+#+)?$/.exec(line);
    if (heading) {
      blocks.push({ type: "heading", text: readableInlineText(heading[1]) });
      continue;
    }
    const item = /^(?:([-+*])\s+|\d+[.)]\s+)(.+)$/.exec(line);
    const previous = blocks.at(-1);
    if (item) {
      const ordered = !item[1];
      if (previous?.type === "list" && previous.ordered === ordered) {
        previous.items.push(readableInlineText(item[2]));
      } else {
        blocks.push({
          type: "list",
          ordered,
          items: [readableInlineText(item[2])],
        });
      }
    } else {
      const content = readableInlineText(line.replace(/^>\s?/, ""));
      if (previous?.type === "paragraph" && previous.text)
        previous.text += `\n${content}`;
      else blocks.push({ type: "paragraph", text: content });
    }
  }
  if (code) blocks.push({ type: "code", text: code.join("\n") });
  return blocks.filter(
    (block) => block.type === "list" || block.text.length > 0,
  );
}

export function readableDocumentText(text: string): string {
  return documentBlocks(text)
    .map((block) =>
      block.type === "list"
        ? block.items
            .map(
              (item, index) =>
                `${block.ordered ? `${index + 1}.` : "-"} ${item}`,
            )
            .join("\n")
        : block.text,
    )
    .join("\n\n");
}
