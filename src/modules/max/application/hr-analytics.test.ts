import assert from "node:assert/strict";
import { test } from "node:test";
import {
  aggregateQuestionErrors,
  describeAttemptQuestions,
  reportCsv,
  shouldRemind,
  studyStatus,
} from "./hr-analytics";

const question = {
  id: "q",
  prompt: "Выберите A",
  type: "SINGLE_CHOICE",
  config: JSON.stringify({ options: ["A", "B"], correctIndex: 0 }),
};

test("HR reads saved shuffled options and does not score unfinished attempts", () => {
  const shuffled = {
    ...question,
    config: JSON.stringify({ options: ["B", "A"], correctIndex: 1 }),
  };
  const attempts = [
    {
      questionSnapshot: JSON.stringify([question]),
      answers: '{"q":1}',
      outcome: "FAILED",
    },
    {
      questionSnapshot: JSON.stringify([shuffled]),
      answers: '{"q":1}',
      outcome: "PASSED",
    },
    {
      questionSnapshot: JSON.stringify([question]),
      answers: "{}",
      outcome: "IN_PROGRESS",
    },
  ];
  assert.deepEqual(aggregateQuestionErrors(attempts), [
    { prompt: "Выберите A", correct: "A", answered: 2, incorrect: 1 },
  ]);
  assert.equal(
    describeAttemptQuestions(
      attempts[2].questionSnapshot,
      "{}",
      "IN_PROGRESS",
    )[0].isCorrect,
    null,
  );
  assert.deepEqual(describeAttemptQuestions("bad", "{}", "FAILED"), []);
});

test("CSV escapes formulas, control-prefixed formulas, separators and quotes", () => {
  const csv = reportCsv([["=SUM(A1)", "\t@formula", 'a;"b', 10]]);
  assert.equal(csv, '\uFEFF"\'=SUM(A1)";"\'\t@formula";"a;""b";"10"');
});

test("new documents do not erase a certificate but make current training pending", () => {
  assert.equal(studyStatus(true, 1, new Date(0)), "OVERDUE");
  assert.equal(studyStatus(true, 1, null), "UPDATED_DOCUMENTS");
  assert.equal(studyStatus(true, 0, new Date(0)), "COMPLETED");
  assert.equal(shouldRemind(null, new Date()), false);
  assert.equal(shouldRemind(new Date(0), new Date()), true);
});
