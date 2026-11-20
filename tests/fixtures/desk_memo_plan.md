# Desk memo editor and commented export

## PR plan

| # | PR | Scope and key tests | Depends on |
|---|---|---|---|
| 1 | Resolve each LEAD's data through a scope id | `Lead.scope_id` (= `id`), `app/scope.py`, call sites switched. No behavior change; all suites pass unchanged. | - |
| 2 | Make desk memos documents that own their LEAD's data | `kind`, `desk_memo_id`, migration 0012, auto-memo, `list_leads(kind)`, delete rules, shared overrides/findings/uploads, fixtures, `ToolDeps.scope_id`, Revert. Tests: migration moves rows and keeps review findings; a LEAD override shows in the memo; two LEADs share facts; a v2 uploaded from a LEAD lands on the memo; FTAI Re-verify matches main. | 1 |
| 3 | Build the desk memo's blocks from its latest version | Derived template, `other_types`, `build_element`, fact matching, `sync_desk_memo` (rebuild only), block fields, draft job, seed. Tests: FTAI P&L cites its facts; every unedited block renders its element text exactly; identical engines match in order; a single mention takes the first equal fact; v2 rebuilds. | 2 |
| 4 | Let the agent work on desk memos and draft LEADs from them | `prompt_context` per kind, assistant prompt branch, `desk_memo_context` from memo blocks, rationale as `edit_note`. Tests: memo chat proposes a memo block edit; LEAD context has the edit, never v1. | 3 |
| 5 | List desk memos in the navigation and create them | Nav group, overview card, dialog modes, `TABS_BY_KIND`, crumb, header. Playwright: create a memo from the FTAI file; Create LEAD lands on a linked LEAD. | 2 |
| 6 | Show the memo in the Memo tab and edit tables as text | Memo tab additions, element findings, Sources groups, table Edit (both kinds), edit reason. Playwright: edit a narrative and a cell; LEAD table edit keeps untouched chips. | 3, 5 |
| 7 | Comment on a desk memo and carry comments forward | `Comment` and migration 0013, anchoring, CRUD, `edit_suggestions`, freeze and carry in sync, composer, drawer and Findings actions, pills. Tests: each anchor target; `edit_suggestions` cases; on v2, edits freeze to v1, a quote carries, an applied suggestion stays. Playwright: quote "$150MM" and suggest "$140MM". | 6 |
| 8 | Export the desk's memo with comments | `@check(desk=...)`, `body_range`, `export_items`, `render_memo_docx`, `docx_review.py`, `.doc` conversion, routes, Comments tab. Tests (read back with python-docx): each kind on the right runs with the right text; a mid-run quote keeps bold; options filter kinds; non-memo findings left out; body text unchanged; a PDF version exports a rebuilt file. Playwright: download matches the preview. | 7 |
| 9 | Let the agent propose comments and changes | `propose_comment`, comment cards (proposals keyed by block and tool). Tests: rejects a missing quote or uncited fact; Add makes an `agent` comment. | 7 |

PR 4 runs in parallel with 5 and 6, and PR 9 with 8. Docs go with the PRs that change them: [`desk-memos.md`](https://github.com/tonyhieu/boa-lead-creation/blob/main/docs/extending/desk-memos.md) (2, 3, 7, 8), [`export.md`](https://github.com/tonyhieu/boa-lead-creation/blob/main/docs/extending/export.md) and [`checks.md`](https://github.com/tonyhieu/boa-lead-creation/blob/main/docs/extending/checks.md) (8), and [`ui-blocks.md`](https://github.com/tonyhieu/boa-lead-creation/blob/main/docs/extending/ui-blocks.md) (6).

Out of scope: Word tracked changes, comment replies, importing the desk's own comments, per-analyst authors, and model-drafted memo content.
