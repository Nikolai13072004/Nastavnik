export type KnowledgeSource = {
  documentId: string;
  documentHash: string;
  title: string;
  section: string;
  pageStart: number | null;
  pageEnd: number | null;
  snippet: string;
  courseDocumentId?: string;
};

export type KnowledgeAnswer = { answer: string; refused: boolean; sources: KnowledgeSource[] };

export type AuthorizedDocument = {
  id: string;
  contentHash: string;
  contentText: string;
  courseDocumentId?: string;
  title?: string;
};

function normalizePassage(text: string): string {
  return text
    .replace(/\u00ad\s*/gu, "")
    .replace(/(\p{L})-\s*\n\s*(\p{L})/gu, "$1$2")
    .replace(/\s+/gu, " ")
    .trim();
}

function sourceMatchesText(source: KnowledgeSource, contentText: string): boolean {
  const snippet = normalizePassage(source.snippet);
  const sectionPrefix = source.section ? `[${source.section}] ` : "";
  const passage = sectionPrefix && snippet.startsWith(sectionPrefix)
    ? snippet.slice(sectionPrefix.length)
    : snippet;
  return Boolean(passage) && normalizePassage(contentText).includes(passage);
}

export function verifyKnowledgeAnswer(answer: KnowledgeAnswer, allowedDocuments: AuthorizedDocument[]): KnowledgeAnswer {
  const refusal: KnowledgeAnswer = {
    answer: "В доступных документах нет подтверждённого ответа на этот вопрос.",
    refused: true,
    sources: [],
  };
  if (answer.refused || answer.sources.length === 0) return refusal;
  const allowed = new Map(allowedDocuments.map((document) => [document.id, document]));
  if (answer.sources.some((source) => {
    const document = allowed.get(source.documentId);
    return !document || !source.documentHash || document.contentHash !== source.documentHash ||
      !sourceMatchesText(source, document.contentText);
  })) {
    return refusal;
  }
  return {
    ...answer,
    sources: answer.sources.map((source) => {
      const verifiedSource = { ...source };
      // The local document ID comes from authorization, never from the model.
      delete verifiedSource.courseDocumentId;
      const document = allowed.get(source.documentId)!;
      if (document.courseDocumentId) verifiedSource.courseDocumentId = document.courseDocumentId;
      if (document.title?.trim()) verifiedSource.title = document.title;
      return verifiedSource;
    }),
  };
}
