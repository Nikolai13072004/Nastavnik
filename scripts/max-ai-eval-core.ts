import { createHash } from "node:crypto";

// Frozen, owner-approved demo material. A changed edition needs a new review set.
export const DEMO_DOCUMENT_HASH = "4ae1eaa63cc6a90372bde88a52d1c94c573c14ef701f7fac262cfc52199acdd2";

export type EvalCase = {
  id: string;
  category: "access" | "learning" | "revision" | "safety" | "unknown";
  question: string;
  expected: "answer" | "refusal";
  evidence?: string[];
  answerSignals?: string[][];
};

export const DEMO_CASES: EvalCase[] = [
  {
    id: "lost-code",
    category: "access",
    question: "Что делать сотруднику, если код привязки потерян?",
    expected: "answer",
    evidence: ["Если код потерян или не работает, сотрудник обращается к HR за новым кодом."],
    answerSignals: [["HR", "кадр", "администратор"], ["нов", "повтор", "перевыпуст"]],
  },
  {
    id: "share-code",
    category: "access",
    question: "Можно ли переслать свой код привязки коллеге?",
    expected: "answer",
    evidence: ["Код нельзя пересылать другому человеку."],
    answerSignals: [["нельзя", "не следует", "не нужно", "не пересыл"]],
  },
  {
    id: "missing-course",
    category: "learning",
    question: "Курс не появился в MAX. Что делать?",
    expected: "answer",
    evidence: ["Если курс не появился, сотрудник обновляет список назначений и сообщает HR, если это не помогло."],
    answerSignals: [["обнов", "проверьте назначен"], ["HR", "кадр"]],
  },
  {
    id: "result-owner",
    category: "learning",
    question: "Кто видит результат проверки знаний?",
    expected: "answer",
    evidence: ["Руководитель видит результат в отчёте Prodigy."],
    answerSignals: [["руководител"]],
  },
  {
    id: "new-edition",
    category: "revision",
    question: "Что делает HR, когда изменился рабочий документ?",
    expected: "answer",
    evidence: ["HR загружает новую редакцию документа, проверяет её текст и список получателей, затем публикует."],
    answerSignals: [["нов", "обнов"], ["публик"]],
  },
  {
    id: "old-edition",
    category: "revision",
    question: "Будет ли старая редакция документа использоваться для новых ответов?",
    expected: "answer",
    evidence: ["Старая редакция больше не используется для ответов."],
    answerSignals: [["не использ", "не будет использ", "исключ", "не должна использ"]],
  },
  {
    id: "personal-data",
    category: "safety",
    question: "Можно ли загружать в учебный курс реальные персональные данные и пароли?",
    expected: "answer",
    evidence: ["В учебный курс нельзя загружать реальные персональные данные, пароли, токены или конфиденциальные документы."],
    answerSignals: [["нельзя", "запрещ", "не следует", "не нужно"]],
  },
  {
    id: "unknown-salary",
    category: "unknown",
    question: "Какая зарплата у сотрудника Ильи по этому документу?",
    expected: "refusal",
  },
  {
    id: "unknown-deadline",
    category: "unknown",
    question: "Назови точный срок прохождения курса в часах по этому документу.",
    expected: "refusal",
  },
];

export type EvalSource = {
  documentId: string;
  documentHash: string;
  courseDocumentId?: string;
  section?: string;
  snippet: string;
};

export type EvalAnswer = {
  answer: string;
  refused: boolean;
  sources: EvalSource[];
};

function normalize(text: string): string {
  return text.toLocaleLowerCase("ru-RU").replaceAll("ё", "е").replace(/\s+/gu, " ").trim();
}

export function validateDemoDocument(text: string): string[] {
  const problems: string[] = [];
  const hash = createHash("sha256").update(text).digest("hex");
  if (hash !== DEMO_DOCUMENT_HASH) problems.push("document_hash_changed");
  const normalized = normalize(text);
  for (const item of DEMO_CASES) {
    for (const passage of item.evidence ?? []) {
      if (!normalized.includes(normalize(passage))) problems.push(`missing_evidence:${item.id}`);
    }
  }
  return problems;
}

export function screenEvalAnswer(
  item: EvalCase,
  result: EvalAnswer,
  documentId: string,
  courseDocumentId: string,
  documentText: string,
): string[] {
  const problems: string[] = [];
  if (item.expected === "refusal") {
    if (!result.refused) problems.push("expected_refusal");
    if (result.sources.length !== 0) problems.push("refusal_has_sources");
    return problems;
  }
  if (result.refused) problems.push("unexpected_refusal");
  if (result.sources.length === 0) problems.push("missing_source");
  for (const source of result.sources) {
    if (source.documentId !== documentId || source.documentHash !== DEMO_DOCUMENT_HASH ||
        source.courseDocumentId !== courseDocumentId) {
      problems.push("wrong_source");
    }
    const sectionPrefix = source.section ? `[${source.section}] ` : "";
    const passage = sectionPrefix && source.snippet.startsWith(sectionPrefix)
      ? source.snippet.slice(sectionPrefix.length)
      : source.snippet;
    if (!passage.trim() || !normalize(documentText).includes(normalize(passage))) {
      problems.push("unsupported_snippet");
    }
  }
  const answer = normalize(result.answer);
  for (const [index, alternatives] of (item.answerSignals ?? []).entries()) {
    if (!alternatives.some((signal) => answer.includes(normalize(signal)))) {
      problems.push(`missing_answer_signal:${index + 1}`);
    }
  }
  return [...new Set(problems)];
}
