// Запуск unit-тестов без ручного списка файлов.
//
// Раньше все пути были перечислены строкой в `package.json`: новый тест не
// подхватывался, пока его туда не допишут, а забытый файл выглядел как
// покрытый код. Glob-паттерны (`--test "src/**/*.test.ts"`) эту проблему
// решают, но `node --test` понимает их не во всех версиях — в Node 20, на
// котором собран образ в `Dockerfile`, их нет. Поэтому файлы ищутся здесь.
//
// Пустой результат — это ошибка, а не успех: молча «пройденный» прогон без
// единого теста ровно то, ради чего этот файл и появился.

import { spawn } from "node:child_process";
import { readdirSync } from "node:fs";
import path from "node:path";

const ROOTS = ["src", "scripts"];
const TEST_FILE = /\.test\.(?:ts|mts|cts|mjs|cjs|js)$/;
// Инфра-тесты (`*.infra.test.*`) требуют реальную БД и запускаются отдельно
// (`npm run test:infra`), чтобы быстрый unit-прогон оставался без БД.
const INFRA_FILE = /\.infra\.test\./;
const SKIP_DIRS = new Set(["node_modules", ".next", "dist", "build", "coverage"]);

function collect(dir, found = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      collect(path.join(dir, entry.name), found);
      continue;
    }
    if (TEST_FILE.test(entry.name) && !INFRA_FILE.test(entry.name)) {
      found.push(path.join(dir, entry.name));
    }
  }
  return found;
}

const files = ROOTS.flatMap((root) => collect(root)).sort();

if (files.length === 0) {
  console.error(`run-unit-tests: не найдено ни одного *.test.* в ${ROOTS.join(", ")}`);
  process.exit(1);
}

console.log(`run-unit-tests: файлов с тестами — ${files.length}`);

// Avoid cmd.exe's 8191-character limit as the list of test files grows.
const child = spawn(process.execPath, ["--import", "tsx", "--test", "--test-concurrency=2", ...files], {
  stdio: "inherit",
});

child.on("error", (error) => {
  console.error(`run-unit-tests: не удалось запустить tsx — ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  process.exit(code ?? (signal ? 1 : 0));
});
