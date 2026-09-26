import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildBotCourseMenu,
  createLoadBotCourses,
  parseCourseLaunch,
} from "./bot-course-menu";
import type { MaxLearnerRepository } from "./list-courses";

const identity = {
  maxUserId: "123",
  userId: "employee",
  organizationId: "company",
  linkedAt: "today",
};

test("bot menu uses current identity and only active assignments", async () => {
  const repository: MaxLearnerRepository = {
    async findIdentity(id) {
      assert.equal(id, "123");
      return identity;
    },
    async findAssignedCourses(userId, organizationId) {
      assert.equal(userId, "employee");
      assert.equal(organizationId, "company");
      return [
        {
          id: "active",
          title: "Current",
          directExpiries: [null],
          groupExpiries: [],
        },
        {
          id: "expired",
          title: "Expired",
          directExpiries: [new Date(0)],
          groupExpiries: [],
        },
      ];
    },
  };
  assert.deepEqual(await createLoadBotCourses(repository)("123"), [
    { id: "active", title: "Current", expiresAt: null },
  ]);
});

test("unlinked or revoked identity cannot load courses", async () => {
  for (const revoked of [false, true]) {
    let reads = 0;
    const repository: MaxLearnerRepository = {
      async findIdentity() {
        return revoked && reads++ === 0 ? identity : null;
      },
      async findAssignedCourses() {
        assert.fail("must not read an unauthorized account");
      },
    };
    assert.equal(await createLoadBotCourses(repository)("123"), null);
  }
});

test("menu is bounded, plaintext and routes each short button to its exact course", () => {
  const courses = Array.from({ length: 20 }, (_, index) => ({
    id: `course-${index}`,
    title: "A\nB\tC" + "x".repeat(500),
    expiresAt: null,
  }));
  const menu = buildBotCourseMenu(courses, "example_bot", "course-12");
  assert.ok(menu.text.length < 4000);
  assert.equal(menu.buttons.length, 11);
  assert.deepEqual(menu.buttons[0], [
    {
      type: "open_app",
      text: "Курс 1",
      web_app: "example_bot",
      payload: "course_course-12",
    },
  ]);
  assert.match(menu.text, /Остальные курсы/);
  assert.ok(!menu.text.includes("A\nB"));
  assert.equal(parseCourseLaunch(menu.buttons[0][0].payload), "course-12");
  assert.match(menu.buttons[0][0].payload, /^[a-zA-Z0-9_-]+$/);
});

test("empty menu and malformed launch data do not expose a course", () => {
  assert.match(buildBotCourseMenu([], "example_bot").text, /нет доступных/);
  for (const value of [
    null,
    undefined,
    "",
    "course_",
    "course:legacy",
    "course_../file",
    "course_a?token=x",
    "course_" + "x".repeat(129),
  ]) {
    assert.equal(parseCourseLaunch(value), undefined);
  }
});
