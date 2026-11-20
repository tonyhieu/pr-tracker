// Pure matchers for the pi extension; tested with `node --test pi/hooks.test.ts`.

// Splits a shell command into simple commands, so `cd x && git ...` is matched per part.
const SEPARATOR_RE = /&&|\|\||[;|\n]/;
const BRANCH_RES = [
  /^git\s+(?:-C\s+\S+\s+)*worktree\s+add\s+(?:.*\s)?-b\s+(\S+)/,
  /^git\s+(?:-C\s+\S+\s+)*checkout\s+(?:.*\s)?-b\s+(\S+)/,
  /^git\s+(?:-C\s+\S+\s+)*switch\s+(?:.*\s)?-c\s+(\S+)/,
];
const PR_URL_RE = /https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+/g;
// Same heading rule as prt/parser.py SECTION_RE.
const PLAN_HEADING_RE = /^##\s+PR\s+(plan|order)\s*$/im;

function unquote(s: string): string {
  return s.replace(/^(["'])(.*)\1$/, "$2");
}

/** `{spec, number}` from `/pr-implement <spec> <n> [-o]` or `/skill:pr-implement ...`. */
export function parseImplementInput(text: string): { spec: string; number: string } | null {
  const m = /^\/(?:skill:)?pr-implement\s+(.*)$/.exec(text.trim().split("\n")[0]);
  if (!m) return null;
  // Whitespace-separated args; single or double quotes keep spaces in a path.
  const args = [...m[1].matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)]
    .map((t) => t[1] ?? t[2] ?? t[3])
    .filter((a) => a && !a.startsWith("-"));
  return args.length >= 2 ? { spec: args[0], number: args[1] } : null;
}

/** The new branch from `git worktree add -b`, `git checkout -b`, or `git switch -c`. */
export function parseBranch(command: string): string | null {
  let branch: string | null = null;
  for (const part of command.split(SEPARATOR_RE)) {
    for (const re of BRANCH_RES) {
      const m = re.exec(part.trim());
      if (m) branch = unquote(m[1]);
    }
  }
  return branch;
}

/** The PR URL printed by a `gh pr create` command, or null for any other command. */
export function parsePrUrl(command: string, output: string): string | null {
  if (!/\bgh\s+pr\s+create\b/.test(command)) return null;
  return output.match(PR_URL_RE)?.at(-1) ?? null;
}

export function hasPlanHeading(content: string): boolean {
  return PLAN_HEADING_RE.test(content);
}
