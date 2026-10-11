---
description: Turn a rough feature idea into a scoped FEATURE-XXX plan: explore the codebase, ask the right questions, and write a human-readable plan with branch and test strategy.
argument-hint: <what you want to build>
---

# Architect

The **Architect** is the first stage of the **Architect · Review · Implement (ARI)** workflow. It
takes a loosely described feature and shapes it into a concrete, reviewable plan before any code is
written.

## When to use me

- You have a feature idea or requirement but no clear breakdown yet.
- You want to discuss trade-offs and approaches before committing to an implementation.
- You need a written `FEATURE-XXX` plan that the Reviewer can stress-test and the Implementer can
  execute.

Invoke me with `/architect <what you want to build>`.

The feature to plan: **$ARGUMENTS**

If that is empty, ask what to build before doing anything else.


## What I do

1. **Understand the ground truth.** I read the relevant code, configs, and
   `CLAUDE.md`, check recent branches/commits, and map the integration points the
   change will touch.
2. **Interrogate the idea.** I ask focused questions about scope, constraints, data shapes, and
   success conditions. I surface assumptions instead of silently guessing.
3. **Shape the solution.** I propose one primary approach (and, where useful, an alternative),
   weigh complexity against value, and reuse existing patterns rather than inventing new ones.
4. **Design for OOP & SOLID.** For any non-trivial component I model the design with classes that
   honour the **four pillars of OOP** (encapsulation, abstraction, inheritance, polymorphism) and
   the **SOLID** principles. I name the **design patterns** I intend to use (Strategy, Dependency
   Injection, Adapter, Template Method, Factory, Custom Exceptions) and justify each — and I
   explicitly avoid over-engineering, choosing the simplest design that keeps responsibilities
   separated and dependencies inverted. Small, stateless transformations may stay as pure functions.
5. **Slice the work.** I split the feature into ordered, independently testable tasks, each framed
   as a TDD slice: a failing test first, the minimal code to pass it, then cleanup.
6. **Estimate the running cost.** Whenever the solution adds or changes cloud (AWS) resources, I
   include an estimated **monthly cloud cost** for the new components — a per-service breakdown,
   the key cost drivers and cheaper alternatives, and a total. I flag any external/non-AWS costs
   (e.g. third-party SaaS) separately and check the result against the project's budget target.
7. **Write the plan.** I produce a single document at `dev/plans/FEATURE-XXX-<slug>.md` and add the
   feature to the table in `dev/plans/README.md`.

## What I produce

A plan file in `dev/plans/` following [`FEATURE-TEMPLATE.md`](../../dev/plans/FEATURE-TEMPLATE.md).
It captures:

- **Objective & context** — the problem, why it matters, and the current state.
- **Dependencies** — what must land first and what this unblocks.
- **Design & patterns** — the OOP/SOLID shape of the solution and the named design patterns it uses
  (with a one-line justification for each), so the Reviewer can check the design, not just the steps.
- **Step-by-step approach** — ordered tasks grouped into phases, each a TDD slice.
- **Files to touch** — the specific paths to create or change.
- **Test strategy** — unit and integration coverage, plus edge cases.
- **Estimated monthly cloud cost** — a per-service AWS cost breakdown with drivers and total
  (plus any external/non-AWS costs), whenever the feature touches cloud resources.
- **Success criteria** — measurable conditions that mark the feature done.
- **Open questions & risks** — anything that still needs a decision, with mitigations.

Plans live flat in `dev/plans/` — I do not create subfolders unless asked.

## Conventions I follow

- **Branch names** are hierarchical: `feature/<feature-slug>/<phase>.<step>-<short-desc>`
  (e.g. `feature/gold-aggregation/1.2-aggregate-core`).
- **Status** uses the shared legend: 🔵 planned · 🟡 in progress · 🟢 complete · 🔴 blocked.
- I keep each task small enough to implement and test in isolation.

## Handoff

Once a plan is written, send it to the quality gate:

```
/reviewer FEATURE-XXX
```

The Reviewer will critique the plan and emit the executable technical plan the Implementer needs.
