import path from "path";
import prisma from "@/lib/prisma";
import {
  parseAttemptAnswers,
  parseFileAnswer,
  parseQuestionSnapshot,
} from "@/lib/quiz-manual-review";
import { parseQuizQuestionMediaFromConfig } from "@/lib/quiz-question-media";
import { storage, type StorageArea } from "@/lib/storage";

const STORAGE_AREA_LABELS = {
  uploads: "Учебные загрузки",
  branding: "Брендинг платформы",
} as const;

const STORAGE_CATEGORY_LABELS = {
  video: "Видео",
  image: "Изображения",
  other: "Прочее",
} as const;

const IMAGE_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  ".svg",
  ".ico",
  ".bmp",
  ".avif",
]);
const VIDEO_EXTENSIONS = new Set([
  ".mp4",
  ".webm",
  ".mov",
  ".m4v",
  ".avi",
  ".mkv",
]);

const STORAGE_AREAS = ["uploads", "branding"] as const satisfies StorageArea[];

type StorageAreaKey = keyof typeof STORAGE_AREA_LABELS;
export type StorageCategoryKey = keyof typeof STORAGE_CATEGORY_LABELS;

export type StorageFileUsage = {
  key: string;
  kind:
    | "course_material"
    | "course_cover"
    | "presentation_preview"
    | "quiz_question_media"
    | "quiz_attachment"
    | "platform_logo"
    | "platform_favicon"
    | "user_avatar";
  label: string;
  detail: string;
  href: string | null;
  courseId: string | null;
  courseTitle: string | null;
  itemId: string | null;
  itemTitle: string | null;
};

export type StorageFileEntry = {
  fileName: string;
  relativePath: string;
  url: string;
  sizeBytes: number;
  extension: string;
  storageArea: StorageAreaKey;
  storageAreaLabel: string;
  category: StorageCategoryKey;
  categoryLabel: string;
  isReferenced: boolean;
  usages: StorageFileUsage[];
};

export type StorageMetadataOrphan = {
  id: string;
  fileName: string;
  relativePath: string;
  url: string;
  sizeBytes: number;
  extension: string;
  storageArea: StorageAreaKey;
  storageAreaLabel: string;
  lastSeenAt: Date;
};

export type StorageOverview = {
  summary: {
    totalFiles: number;
    totalSizeBytes: number;
    referencedFiles: number;
    referencedSizeBytes: number;
    unreferencedFiles: number;
    unreferencedSizeBytes: number;
    metadataOrphanRecords: number;
  };
  categorySummaries: Array<{
    key: StorageCategoryKey;
    label: string;
    filesCount: number;
    sizeBytes: number;
  }>;
  areaSummaries: Array<{
    key: StorageAreaKey;
    label: string;
    filesCount: number;
    sizeBytes: number;
  }>;
  largestFiles: StorageFileEntry[];
  orphanFiles: StorageFileEntry[];
  metadataOrphans: StorageMetadataOrphan[];
  files: StorageFileEntry[];
};

type ScannedFile = Omit<StorageFileEntry, "isReferenced" | "usages">;

function normalizeLocalUrl(value: string | null | undefined) {
  const trimmed = value?.trim();
  if (!trimmed || !trimmed.startsWith("/")) return null;

  const clean = trimmed.split("?")[0]?.split("#")[0] ?? trimmed;
  return clean || null;
}

export function resolveStorageTarget(urlValue: string) {
  const normalizedUrl = normalizeLocalUrl(urlValue);
  if (!normalizedUrl) return null;

  const target = storage.resolveUrl(normalizedUrl);
  if (target) {
    return {
      url: normalizedUrl,
      relativePath: target.key,
      absolutePath: target.absolutePath,
      storageArea: target.area,
      storageAreaLabel: STORAGE_AREA_LABELS[target.area],
    };
  }

  return null;
}

function getStorageCategory(extension: string): StorageCategoryKey {
  if (VIDEO_EXTENSIONS.has(extension)) return "video";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  return "other";
}

function isStorageAreaKey(value: string): value is StorageAreaKey {
  return value === "uploads" || value === "branding";
}

function buildUsageKey(args: {
  kind: StorageFileUsage["kind"];
  href: string | null;
  detail: string;
  courseId: string | null;
  itemId: string | null;
}) {
  return [
    args.kind,
    args.href ?? "",
    args.detail,
    args.courseId ?? "",
    args.itemId ?? "",
  ].join("|");
}

function addUsage(
  map: Map<string, StorageFileUsage[]>,
  urlValue: string | null | undefined,
  usage: Omit<StorageFileUsage, "key">,
) {
  const url = normalizeLocalUrl(urlValue);
  if (!url) return;

  const list = map.get(url) ?? [];
  const nextItem: StorageFileUsage = {
    ...usage,
    key: buildUsageKey({
      kind: usage.kind,
      href: usage.href,
      detail: usage.detail,
      courseId: usage.courseId,
      itemId: usage.itemId,
    }),
  };

  if (!list.some((item) => item.key === nextItem.key)) {
    list.push(nextItem);
    map.set(url, list);
  }
}

function truncateText(value: string, maxLength = 90) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`;
}

function getCourseItemUsageLabel(itemType: string, fileUrl: string) {
  const extension = path.extname(fileUrl).toLowerCase();
  if (extension === ".pptx") return "Презентация урока";
  if (itemType === "VIDEO") return "Видео урока";
  if (itemType === "PDF") return "Файл урока";
  return "Материал курса";
}

function presentationPreviewUrl(fileUrl: string) {
  return fileUrl.replace(/\.pptx$/i, ".pdf");
}

async function scanStorageArea(
  area: StorageAreaKey,
): Promise<ScannedFile[]> {
  const entries = await storage.list(area);
  return entries.map((entry) => {
    const extension = path.extname(entry.key).toLowerCase();
    const category = getStorageCategory(extension);

    return {
      fileName: path.basename(entry.key),
      relativePath: entry.key,
      url: entry.url,
      sizeBytes: entry.sizeBytes,
      extension,
      storageArea: area,
      storageAreaLabel: STORAGE_AREA_LABELS[area],
      category,
      categoryLabel: STORAGE_CATEGORY_LABELS[category],
    };
  });
}

async function loadUsageMap() {
  const usageMap = new Map<string, StorageFileUsage[]>();

  const [
    courseItems,
    courseCovers,
    quizQuestions,
    quizAttempts,
    platformSettings,
    userAvatars,
    maxDocuments,
  ] = await Promise.all([
    prisma.courseItem.findMany({
      where: {
        fileUrl: {
          startsWith: "/uploads/",
        },
      },
      select: {
        id: true,
        title: true,
        type: true,
        fileUrl: true,
        course: {
          select: {
            id: true,
            title: true,
          },
        },
      },
    }),
    prisma.course.findMany({
      where: {
        coverUrl: {
          startsWith: "/uploads/course-covers/",
        },
      },
      select: {
        id: true,
        title: true,
        coverUrl: true,
      },
    }),
    prisma.question.findMany({
      where: {
        config: {
          contains: "/uploads/quiz-question-media/",
        },
        archivedAt: null,
      },
      select: {
        id: true,
        prompt: true,
        config: true,
        quiz: {
          select: {
            id: true,
            courseItem: {
              select: {
                id: true,
                title: true,
                course: {
                  select: {
                    id: true,
                    title: true,
                  },
                },
              },
            },
          },
        },
      },
    }),
    prisma.quizAttempt.findMany({
      where: {
        OR: [
          {
            answers: {
              contains: "/uploads/quiz-attachments/",
            },
          },
          {
            questionSnapshot: {
              contains: "/uploads/quiz-question-media/",
            },
          },
        ],
      },
      select: {
        id: true,
        answers: true,
        questionSnapshot: true,
        user: {
          select: {
            id: true,
            name: true,
            login: true,
          },
        },
        quiz: {
          select: {
            courseItem: {
              select: {
                id: true,
                title: true,
                course: {
                  select: {
                    id: true,
                    title: true,
                  },
                },
              },
            },
          },
        },
      },
    }),
    prisma.platformSettings.findUnique({
      where: { id: "default" },
      select: {
        siteName: true,
        logoUrl: true,
        faviconUrl: true,
      },
    }),
    prisma.user.findMany({
      where: {
        avatarUrl: {
          startsWith: "/uploads/user-avatars/",
        },
      },
      select: {
        id: true,
        name: true,
        login: true,
        avatarUrl: true,
      },
    }),
    prisma.maxCourseDocument.findMany({
      where: { originalKey: { not: null } },
      select: { id: true, title: true, originalKey: true, courseId: true, course: { select: { title: true } } },
    }),
  ]);

  for (const document of maxDocuments) {
    if (!document.originalKey) continue;
    addUsage(usageMap, storage.url("uploads", document.originalKey), {
      kind: "course_material",
      label: "Оригинал документа MAX",
      detail: `${document.course.title} · ${document.title}`,
      href: "/max",
      courseId: document.courseId,
      courseTitle: document.course.title,
      itemId: document.id,
      itemTitle: document.title,
    });
  }

  for (const item of courseItems) {
    const fileUrl = normalizeLocalUrl(item.fileUrl);
    if (!fileUrl) continue;

    addUsage(usageMap, fileUrl, {
      kind: "course_material",
      label: getCourseItemUsageLabel(item.type, fileUrl),
      detail: `${item.course.title} · ${item.title}`,
      href: `/courses/${item.course.id}/manage`,
      courseId: item.course.id,
      courseTitle: item.course.title,
      itemId: item.id,
      itemTitle: item.title,
    });

    if (/\.pptx$/i.test(fileUrl)) {
      addUsage(usageMap, presentationPreviewUrl(fileUrl), {
        kind: "presentation_preview",
        label: "PDF-превью презентации",
        detail: `${item.course.title} · ${item.title}`,
        href: `/courses/${item.course.id}/manage`,
        courseId: item.course.id,
        courseTitle: item.course.title,
        itemId: item.id,
        itemTitle: item.title,
      });
    }
  }

  for (const course of courseCovers) {
    addUsage(usageMap, course.coverUrl, {
      kind: "course_cover",
      label: "Обложка курса",
      detail: course.title,
      href: `/courses/${course.id}/manage`,
      courseId: course.id,
      courseTitle: course.title,
      itemId: null,
      itemTitle: null,
    });
  }

  for (const question of quizQuestions) {
    const media = parseQuizQuestionMediaFromConfig(question.config);
    if (!media) continue;

    const courseId = question.quiz.courseItem.course.id;
    const courseTitle = question.quiz.courseItem.course.title;
    const itemId = question.quiz.courseItem.id;
    const itemTitle = question.quiz.courseItem.title;

    addUsage(usageMap, media.url, {
      kind: "quiz_question_media",
      label: "Медиа вопроса",
      detail: [courseTitle, itemTitle, truncateText(question.prompt, 72)]
        .filter(Boolean)
        .join(" · "),
      href: `/courses/${courseId}/quiz/${question.quiz.id}/builder`,
      courseId,
      courseTitle,
      itemId,
      itemTitle,
    });
  }

  for (const attempt of quizAttempts) {
    const answers = parseAttemptAnswers(attempt.answers);
    const questions = parseQuestionSnapshot(attempt.questionSnapshot);
    const promptByQuestionId = new Map(
      questions.map((question) => [question.id, question.prompt]),
    );
    const learnerLabel = attempt.user.name || attempt.user.login;
    const courseId = attempt.quiz.courseItem.course.id;
    const courseTitle = attempt.quiz.courseItem.course.title;
    const itemId = attempt.quiz.courseItem.id;
    const itemTitle = attempt.quiz.courseItem.title;

    for (const question of questions) {
      const media = parseQuizQuestionMediaFromConfig(question.config);
      if (!media) continue;

      addUsage(usageMap, media.url, {
        kind: "quiz_question_media",
        label: "Медиа вопроса в попытке",
        detail: [
          courseTitle,
          itemTitle,
          learnerLabel,
          truncateText(question.prompt, 72),
        ]
          .filter(Boolean)
          .join(" · "),
        href: `/courses/${courseId}/manage`,
        courseId,
        courseTitle,
        itemId,
        itemTitle,
      });
    }

    for (const [questionId, answer] of Object.entries(answers)) {
      const fileAnswer = parseFileAnswer(answer);
      if (!fileAnswer) continue;

      const prompt = promptByQuestionId.get(questionId);
      const detailParts = [
        courseTitle,
        itemTitle,
        learnerLabel,
        prompt ? truncateText(prompt, 72) : null,
      ].filter(Boolean);

      addUsage(usageMap, fileAnswer.url, {
        kind: "quiz_attachment",
        label: "Вложение ответа",
        detail: detailParts.join(" · "),
        href: `/courses/${courseId}/manage`,
        courseId,
        courseTitle,
        itemId,
        itemTitle,
      });
    }
  }

  if (platformSettings?.logoUrl) {
    addUsage(usageMap, platformSettings.logoUrl, {
      kind: "platform_logo",
      label: "Логотип платформы",
      detail: platformSettings.siteName || "Общие настройки",
      href: "/admin/settings?tab=general",
      courseId: null,
      courseTitle: null,
      itemId: null,
      itemTitle: null,
    });
  }

  if (platformSettings?.faviconUrl) {
    addUsage(usageMap, platformSettings.faviconUrl, {
      kind: "platform_favicon",
      label: "Favicon платформы",
      detail: platformSettings.siteName || "Общие настройки",
      href: "/admin/settings?tab=general",
      courseId: null,
      courseTitle: null,
      itemId: null,
      itemTitle: null,
    });
  }

  for (const user of userAvatars) {
    const avatarUrl = normalizeLocalUrl(user.avatarUrl);
    if (!avatarUrl) continue;

    addUsage(usageMap, avatarUrl, {
      kind: "user_avatar",
      label: "Аватар пользователя",
      detail: `${user.name} · ${user.login}`,
      href: `/admin/users/${user.id}/edit`,
      courseId: null,
      courseTitle: null,
      itemId: null,
      itemTitle: null,
    });
  }

  return usageMap;
}

export async function getStorageOverview(): Promise<StorageOverview> {
  const [usageMap, storageRecords, ...scannedAreas] = await Promise.all([
    loadUsageMap(),
    prisma.storageFile.findMany({
      select: {
        id: true,
        area: true,
        key: true,
        url: true,
        sizeBytes: true,
        extension: true,
        lastSeenAt: true,
      },
    }),
    ...STORAGE_AREAS.map((area) => scanStorageArea(area)),
  ]);

  const files = scannedAreas
    .flat()
    .map((file) => {
      const usages = [...(usageMap.get(file.url) ?? [])].sort((left, right) => {
        if (left.label !== right.label) {
          return left.label.localeCompare(right.label, "ru", {
            sensitivity: "base",
          });
        }
        return left.detail.localeCompare(right.detail, "ru", {
          sensitivity: "base",
        });
      });

      return {
        ...file,
        usages,
        isReferenced: usages.length > 0,
      } satisfies StorageFileEntry;
    })
    .sort((left, right) => {
      if (left.sizeBytes !== right.sizeBytes)
        return right.sizeBytes - left.sizeBytes;
      return left.fileName.localeCompare(right.fileName, "ru", {
        sensitivity: "base",
        numeric: true,
      });
    });

  const diskKeys = new Set(files.map((file) => `${file.storageArea}:${file.relativePath}`));
  const metadataOrphans = storageRecords
    .flatMap((record) => {
      if (!isStorageAreaKey(record.area) || diskKeys.has(`${record.area}:${record.key}`)) {
        return [];
      }

      return [
        {
          id: record.id,
          fileName: path.basename(record.key),
          relativePath: record.key,
          url: record.url,
          sizeBytes: Number(record.sizeBytes),
          extension: record.extension,
          storageArea: record.area,
          storageAreaLabel: STORAGE_AREA_LABELS[record.area],
          lastSeenAt: record.lastSeenAt,
        },
      ];
    })
    .sort((left, right) =>
      left.relativePath.localeCompare(right.relativePath, "ru", {
        sensitivity: "base",
      }),
    );

  const totalFiles = files.length;
  const totalSizeBytes = files.reduce((sum, file) => sum + file.sizeBytes, 0);
  const referencedFiles = files.filter((file) => file.isReferenced).length;
  const referencedSizeBytes = files
    .filter((file) => file.isReferenced)
    .reduce((sum, file) => sum + file.sizeBytes, 0);
  const unreferencedFiles = totalFiles - referencedFiles;
  const unreferencedSizeBytes = totalSizeBytes - referencedSizeBytes;

  const categorySummaries = (
    Object.entries(STORAGE_CATEGORY_LABELS) as Array<
      [StorageCategoryKey, string]
    >
  ).map(([key, label]) => ({
    key,
    label,
    filesCount: files.filter((file) => file.category === key).length,
    sizeBytes: files
      .filter((file) => file.category === key)
      .reduce((sum, file) => sum + file.sizeBytes, 0),
  }));

  const areaSummaries = (
    Object.entries(STORAGE_AREA_LABELS) as Array<[StorageAreaKey, string]>
  ).map(([key, label]) => ({
    key,
    label,
    filesCount: files.filter((file) => file.storageArea === key).length,
    sizeBytes: files
      .filter((file) => file.storageArea === key)
      .reduce((sum, file) => sum + file.sizeBytes, 0),
  }));
  const orphanFiles = files.filter((file) => !file.isReferenced);

  return {
    summary: {
      totalFiles,
      totalSizeBytes,
      referencedFiles,
      referencedSizeBytes,
      unreferencedFiles,
      unreferencedSizeBytes,
      metadataOrphanRecords: metadataOrphans.length,
    },
    categorySummaries,
    areaSummaries,
    largestFiles: files.slice(0, 20),
    orphanFiles,
    metadataOrphans,
    files,
  };
}

export async function getStorageFileByUrl(urlValue: string) {
  const target = resolveStorageTarget(urlValue);
  if (!target) return null;

  const overview = await getStorageOverview();
  return overview.files.find((file) => file.url === target.url) ?? null;
}
