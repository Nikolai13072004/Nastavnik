type Material = { id: string; title: string; content: string | null };

const STOP_WORDS = new Set([
  "как", "что", "где", "когда", "какой", "какая", "какие", "это", "для", "про", "или", "мне", "нужно",
  "надо", "можно", "ли", "по", "из", "на", "в", "во", "и", "а", "о", "об", "с", "со", "у", "за", "при",
]);

function words(text: string) {
  return [...new Set((text.toLocaleLowerCase("ru-RU").match(/[\p{L}\p{N}]{3,}/gu) ?? [])
    .filter((word) => !STOP_WORDS.has(word)))];
}

function plainText(html: string) {
  return html
    .replace(/<\/(?:p|li|h[1-6]|blockquote)>|<br\s*\/?>/gi, "\n")
    .replace(/<[^>]*>/g, " ")
    .replace(/&(?:nbsp|#160);/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#(?:39|x27);/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s+/g, "\n")
    .trim();
}

export type CoursePassage = { materialId: string; materialTitle: string; excerpt: string };

export function findCoursePassage(question: string, materials: Material[]): CoursePassage | null {
  const query = words(question);
  if (query.length === 0) return null;

  let best: { passage: CoursePassage; score: number } | null = null;
  for (const material of materials) {
    if (!material.content) continue;
    const paragraphs = plainText(material.content).split(/\n+/).map((part) => part.trim()).filter(Boolean);
    for (const paragraph of paragraphs) {
      const paragraphWords = words(`${material.title} ${paragraph}`);
      const overlap = query.filter((word) => paragraphWords.includes(word)).length;
      const needed = query.length === 1 ? 1 : 2;
      if (overlap < needed) continue;
      const score = overlap / query.length;
      if (!best || score > best.score) {
        best = {
          passage: {
            materialId: material.id,
            materialTitle: material.title,
            excerpt: paragraph.length > 600 ? `${paragraph.slice(0, 597)}…` : paragraph,
          },
          score,
        };
      }
    }
  }
  return best?.passage ?? null;
}
