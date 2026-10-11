# Architect · Review · Implement — the agent workflow

> The three stages live as Claude Code slash commands in [`.claude/commands/`](commands/).
> They run in the main session, so they can ask you questions and you see every step.

This repository uses a small, three-stage workflow to take a feature from idea to merged code with
Claude Code. Each stage is a slash command with one job and a clear handoff.

```
 idea ──▶  /architect  ──▶  /reviewer  ──▶  /implementer  ──▶  PR
            (plan)          (gate)          (build)
```

| Stage | Command | Produces | Lives in |
| --- | --- | --- | --- |
| **Architect** | [`/architect`](commands/architect.md) | Human-readable feature plan | `dev/plans/FEATURE-XXX-<slug>.md` |
| **Review** | [`/reviewer`](commands/reviewer.md) | Review **+** executable technical plan | `dev/reviews/REVIEW-FEATURE-XXX.md` and `dev/plans/technical/FEATURE-XXX-technical-plan.yaml` |
| **Implement** | [`/implementer`](commands/implementer.md) | Tested code, clean commits | feature branches → PR |

## How a feature flows

1. **Architect** — `/architect I want to <goal>`. The Architect explores the codebase, asks
   clarifying questions, and writes a `FEATURE-XXX` plan plus a row in
   [`dev/plans/README.md`](../dev/plans/README.md). The plan breaks the feature into ordered tasks.
2. **Review** — `/reviewer FEATURE-XXX`. The Reviewer critiques the plan and emits **two**
   files: a review (the reasoning) and a technical plan (the atomic, executable tasks).
3. **Implement** — `/implementer FEATURE-XXX`. The Implementer works the technical plan
   task by task: a branch and a TDD cycle (RED → GREEN → REFACTOR) each, one commit per task,
   status kept in sync.
4. **Ship** — push the branch and open a PR referencing the feature ID.

## The two review artifacts

The handoff from Review to Implement is deliberately split:

- The **review** (`dev/reviews/REVIEW-FEATURE-XXX.md`) explains *why* — verdict, ranked findings,
  risks, effort estimate. Read it for context.
- The **technical plan** (`dev/plans/technical/FEATURE-XXX-technical-plan.yaml`) says *what to do* —
  atomic tasks with acceptance criteria, branch names, commit messages, and file boundaries. This
  is what the Implementer executes.

## Conventions

- **Branches** are hierarchical: `feature/<feature-slug>/<phase>.<step>-<short-desc>`.
- **Status legend:** 🔵 planned · 🟡 in progress · 🟢 complete · 🔴 blocked.
- **Source of truth:** the technical-plan YAML drives task progress; the top-level feature file and
  the plans README mirror it.
- **Commits:** one focused commit per task, using the message from the technical plan.

## What no command does on its own

- **Anything that changes live infrastructure or costs money.** `terraform apply`, `terraform destroy`,
  `terraform state rm`, DNS edits, and deploys run only on an explicit request. Prod applies stay
  manual and local (see [`documentation/PROD_PROMOTION_RUNBOOK.md`](../documentation/PROD_PROMOTION_RUNBOOK.md)).
- **Weakening a reviewed safety gate.** If a gate in a technical plan cannot be met, the Implementer
  stops and asks. The plan changes only on the user's decision, recorded in the progress log.
- **Pushes and PRs.** These happen only when asked.

## Keeping the artifacts consistent

A small checker keeps statuses, feature tables, and CI gate names aligned across the plan, review,
and technical-plan files. Run it before opening a PR:

```bash
python dev/tools/validate_workflow.py
```

It also runs automatically in CI (see
[`.github/workflows/workflow-consistency.yml`](../.github/workflows/workflow-consistency.yml)) on any change
under `.github/`, `.claude/`, or `dev/`.

## Adapting this workflow to another project

The commands read project-specific conventions from
[`CLAUDE.md`](../CLAUDE.md). To reuse this workflow elsewhere, update that
file with the new project's overview, stack, and coding standards — the commands pick the rest up
automatically. Start the templates from
[`dev/plans/FEATURE-TEMPLATE.md`](../dev/plans/FEATURE-TEMPLATE.md) and
[`dev/reviews/REVIEW-TEMPLATE.md`](../dev/reviews/REVIEW-TEMPLATE.md).
