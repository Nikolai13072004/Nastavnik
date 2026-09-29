import type { MaxLearnerIdentity } from "./list-courses";

export const MAX_DOCUMENT_BYTES = 32 * 1024;

export type MaxDocumentDraft = {
  courseId: string;
  title: string;
  sourceName: string;
  contentText: string;
  originalBytes?: Buffer;
  supersedesId?: string;
  changeSummary?: string;
  checkQuestion?: string;
  checkOptions?: string[];
  checkCorrectIndex?: number;
};

export interface MaxDocumentCommands {
  upload(identity: MaxLearnerIdentity, draft: MaxDocumentDraft): Promise<"CREATED" | "FORBIDDEN" | "NOT_FOUND" | "LIMIT_REACHED" | "CONFLICT">;
  setPublished(identity: MaxLearnerIdentity, courseId: string, documentId: string, publish: boolean,
    expectedRecipientIds?: string[]): Promise<"UPDATED" | "FORBIDDEN" | "NOT_FOUND" | "AUDIENCE_CHANGED" | "AUDIENCE_TOO_LARGE" | "CONFLICT">;
}

export function createManageMaxDocuments(repository: MaxDocumentCommands) {
  return {
    async upload(identity: MaxLearnerIdentity, input: MaxDocumentDraft) {
      const draft = {
        courseId: input.courseId.trim(),
        title: input.title.trim(),
        sourceName: input.sourceName.trim(),
        contentText: input.contentText.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").trim(),
        originalBytes: input.originalBytes,
        supersedesId: input.supersedesId?.trim(),
        changeSummary: input.changeSummary?.trim(),
        checkQuestion: input.checkQuestion?.trim(),
        checkOptions: input.checkOptions?.map((option) => option.trim()),
        checkCorrectIndex: input.checkCorrectIndex,
      };
      if (!draft.courseId || draft.courseId.length > 128 || !draft.title || draft.title.length > 120 ||
          !draft.sourceName || draft.sourceName.length > 120 ||
          /[\\/\u0000-\u001F\u007F\uD800-\uDFFF]/u.test(draft.sourceName) ||
          !/\.(txt|md|pdf)$/i.test(draft.sourceName) || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFD]/u.test(draft.contentText) ||
          !draft.contentText || Buffer.byteLength(draft.contentText, "utf8") > MAX_DOCUMENT_BYTES ||
          (draft.originalBytes && (!Buffer.isBuffer(draft.originalBytes) || draft.originalBytes.length === 0 ||
            draft.originalBytes.length > (/\.pdf$/i.test(draft.sourceName) ? 512 * 1024 : MAX_DOCUMENT_BYTES))) ||
          (draft.supersedesId ? (
            draft.supersedesId.length > 128 || !draft.changeSummary || draft.changeSummary.length > 500 ||
            !draft.checkQuestion || draft.checkQuestion.length > 240 ||
            draft.checkOptions?.length !== 3 || draft.checkOptions.some((option) => option.length < 2 || option.length > 160) ||
            !Number.isInteger(draft.checkCorrectIndex) || draft.checkCorrectIndex! < 0 || draft.checkCorrectIndex! > 2
          ) : Boolean(draft.changeSummary || draft.checkQuestion || draft.checkOptions || draft.checkCorrectIndex !== undefined))) {
        return "INVALID_INPUT" as const;
      }
      return repository.upload(identity, draft);
    },
    async setPublished(identity: MaxLearnerIdentity, courseId: string, documentId: string, publish: boolean,
      expectedRecipientIds?: string[]) {
      if (!courseId || courseId.length > 128 || !documentId || documentId.length > 128) {
        return "INVALID_INPUT" as const;
      }
      if (expectedRecipientIds && (expectedRecipientIds.length > 200 ||
          new Set(expectedRecipientIds).size !== expectedRecipientIds.length ||
          expectedRecipientIds.some((id) => !id || id.length > 128))) return "INVALID_INPUT" as const;
      return repository.setPublished(identity, courseId, documentId, publish, expectedRecipientIds);
    },
  };
}
