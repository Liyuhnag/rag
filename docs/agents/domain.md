# Domain Docs

## Layout

This repository uses single-context: one shared GLOSSARY.md at the repository root, with architecture decisions in docs/adr/.

Create domain documents lazily when the first actual terms or decisions are recorded. No GLOSSARY-MAP.md or per-project glossaries are needed for this layout.

## Before exploring, read these

- `GLOSSARY.md` at the repository root, if it exists.
- ADRs in `docs/adr/` that touch the area being explored.

If these files do not exist, proceed silently. Do not suggest creating empty documents upfront. The `domain-modeling` skill creates them when terms or decisions are resolved.

## Use the glossary's vocabulary

When naming domain concepts in issue titles, refactor proposals, hypotheses or tests, use the terms defined in `GLOSSARY.md`.

If a concept is missing, reconsider whether the project uses that language; note real vocabulary gaps for `domain-modeling`.

## Flag ADR conflicts

If an output contradicts an existing ADR, surface the conflict explicitly and explain why the decision may need revisiting.
