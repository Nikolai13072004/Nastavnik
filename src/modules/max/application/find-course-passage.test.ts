import assert from "node:assert/strict";
import test from "node:test";
import { findCoursePassage } from "./find-course-passage";

const materials = [{
  id: "one",
  title: "Первый день",
  content: "<p>Руководитель подтверждает изменения в регламенте перед публикацией.</p><p>Сотрудник проходит тест в MAX.</p>",
}];

test("returns an exact published passage and its material, not a generated answer", () => {
  assert.deepEqual(findCoursePassage("Кто подтверждает изменения в регламенте?", materials), {
    materialId: "one",
    materialTitle: "Первый день",
    excerpt: "Руководитель подтверждает изменения в регламенте перед публикацией.",
  });
});

test("refuses unsupported questions and empty material", () => {
  assert.equal(findCoursePassage("Какой пароль от сервера?", materials), null);
  assert.equal(findCoursePassage("Что?", materials), null);
  assert.equal(findCoursePassage("изменения регламенте", [{ ...materials[0], content: null }]), null);
});

test("returns plain text rather than active markup", () => {
  const result = findCoursePassage("курс безопасность", [{
    id: "two", title: "Курс", content: "<p>Курс про безопасность &amp; доступность.</p><script>alert(1)</script>",
  }]);
  assert.equal(result?.excerpt.includes("<"), false);
});
