import assert from "node:assert/strict";
import { test } from "node:test";

import { hasPlanHeading, parseBranch, parseImplementInput, parsePrUrl } from "./hooks.ts";

test("parseImplementInput reads spec and number", () => {
  assert.deepEqual(parseImplementInput("/pr-implement spec.md 7"), { spec: "spec.md", number: "7" });
  assert.deepEqual(parseImplementInput("/skill:pr-implement ~/GitHub/r/s.md 0.3 -o"), {
    spec: "~/GitHub/r/s.md",
    number: "0.3",
  });
  assert.deepEqual(parseImplementInput("  /pr-implement -o 'my spec.md' 2\nmore text"), {
    spec: "my spec.md",
    number: "2",
  });
});

test("parseImplementInput ignores other input", () => {
  assert.equal(parseImplementInput("/pr-implement spec.md"), null);
  assert.equal(parseImplementInput("/pr-implementx spec.md 1"), null);
  assert.equal(parseImplementInput("please /pr-implement spec.md 1"), null);
  assert.equal(parseImplementInput("/skill:other spec.md 1"), null);
});

test("parseBranch finds new branches", () => {
  assert.equal(parseBranch("git worktree add -b ui ../pr-tracker-ui origin/main"), "ui");
  assert.equal(parseBranch("cd x && git worktree add -b y ../z main"), "y");
  assert.equal(parseBranch("git worktree add ../z -b y main"), "y");
  assert.equal(parseBranch("git -C repo checkout -b 'feat/a'"), "feat/a");
  assert.equal(parseBranch("git fetch; git switch -c fix-1 origin/main"), "fix-1");
});

test("parseBranch ignores commands without a new branch", () => {
  assert.equal(parseBranch("git worktree add ../z main"), null);
  assert.equal(parseBranch("git checkout main"), null);
  assert.equal(parseBranch("git switch fix-1"), null);
  assert.equal(parseBranch("echo git checkout -b nope"), null);
});

test("parsePrUrl takes the URL from gh pr create output", () => {
  const out = "Creating pull request for ui into main\n\nhttps://github.com/o/r/pull/12\n";
  assert.equal(parsePrUrl("git push -u origin ui && gh pr create --title t --body b", out), "https://github.com/o/r/pull/12");
  assert.equal(parsePrUrl("gh pr view 12", out), null);
  assert.equal(parsePrUrl("gh pr create --fill", "no url here"), null);
});

test("hasPlanHeading matches the parser's section headings", () => {
  assert.ok(hasPlanHeading("# T\n\n## PR plan\n\n| # |"));
  assert.ok(hasPlanHeading("## pr order  \n"));
  assert.ok(!hasPlanHeading("## PR plans\n"));
  assert.ok(!hasPlanHeading("### PR plan\n"));
});
