# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues:
https://github.com/Liyuhnag/rag/issues

Use the `gh` CLI. Infer the repository from `git remote -v`.

## Conventions

- **Create**: `gh issue create --title "..." --body-file <file>`.
- **Read**: `gh issue view <number> --comments`; fetch labels as needed.
- **List**: `gh issue list` with appropriate state and label filters.
- **Comment**: `gh issue comment <number> --body-file <file>`.
- **Apply/remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`.
- **Close**: `gh issue close <number> --comment "..."`.

When a skill says "publish to the issue tracker", create a GitHub issue.
When a skill says "fetch the relevant ticket", read the issue and comments.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Wayfinding operations

- **Map**: one issue labelled `wayfinder:map`, with Notes, Decisions-so-far and Fog in its body.
- **Child ticket**: link it to the map as a native GitHub sub-issue. If unavailable, use a task list in the map and put `Part of #<map>` at the top of the child body.
- **Child labels**: `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling` or `wayfinder:task`, according to the ticket type.
- **Blocking**: use native GitHub issue dependencies. POST to `repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by` with `issue_id=<blocker database id>`, not its issue number or node_id. If unavailable, record `Blocked by: #<n>` at the top of the child body. A ticket is unblocked when every blocker is closed.
- **Frontier**: open children with no open blockers and no assignee; choose the first in map order.
- **Claim**: assign the ticket to the driving developer as the first write (`gh issue edit <n> --add-assignee @me` for yourself).
- **Resolve**: comment with the answer, close the ticket, then append a gist and link to the map's Decisions-so-far.

## Label policy

Follow each invoked skill's label rules. This configuration does not require additional labels on every issue.
