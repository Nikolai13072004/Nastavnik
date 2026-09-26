import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "playwright-report/**",
    "test-results/**",
    "output/playwright/**",
    "tmp/**",
  ]),
  {
    files: ["src/modules/**/domain/**/*.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "next",
                "next/*",
                "react",
                "react/*",
                "server-only",
                "@/app/**",
                "@/components/**",
                "@/lib/prisma",
                "@/modules/**/infrastructure/*",
                "@/modules/**/server/*",
              ],
              message: "Доменный слой не должен зависеть от фреймворка или инфраструктуры.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/modules/**/application/**/*.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: [
                "next",
                "next/*",
                "react",
                "react/*",
                "server-only",
                "@/app/**",
                "@/components/**",
                "@/lib/prisma",
                "@/modules/**/infrastructure/*",
                "@/modules/**/server/*",
              ],
              message: "Прикладной слой работает через порты и не импортирует инфраструктуру.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/modules/**/infrastructure/**/*.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/app/**", "@/components/**"],
              message: "Инфраструктурный слой не должен зависеть от web/UI слоя.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/modules/**/server/**/*.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/app/**", "@/components/**"],
              message: "Server facade собирает application и infrastructure, но не зависит от web/UI.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/app/courses/*/manage/**/*.tsx"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/lib/prisma"],
              message: "Management UI получает данные через read-side query-service, а не обращается к Prisma напрямую.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      "src/app/actions/course-settings-actions.ts",
      "src/app/actions/course-content-actions.ts",
    ],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/lib/prisma", "@/modules/**/infrastructure/*"],
              message: "Course Server Actions are transport adapters and must call server/application facades.",
            },
          ],
        },
      ],
    },
  },
  {
    files: ["src/app/actions/course-action-input.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["next", "next/*", "@/lib/prisma", "@/modules/**/infrastructure/*"],
              message: "Transport input normalization must stay pure and independent from Next.js and persistence.",
            },
          ],
        },
      ],
    },
  },
]);

export default eslintConfig;
