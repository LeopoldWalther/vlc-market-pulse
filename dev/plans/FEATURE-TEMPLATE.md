# FEATURE-XXX — <short title>

**Status:** 🔵 Planned · **Effort:** <S/M/L (~Xh)> · **Priority:** <High/Medium/Low>
**Branch root:** `feature/<feature-slug>` · **Created:** YYYY-MM-DD · **Updated:** YYYY-MM-DD

> Authored by `/architect`. Reviewed by `/reviewer` (see `dev/reviews/REVIEW-FEATURE-XXX.md`).
> Implemented by `/implementer` from `dev/plans/technical/FEATURE-XXX-technical-plan.yaml`.

## Objective

One or two sentences: what this delivers and why it's worth doing.

## Context

The current state and the problem being solved. Link to relevant code, data, or prior features.

## Dependencies

- **Needs:** FEATURE-YYY — <reason>
- **Unblocks:** FEATURE-ZZZ — <reason>

## Approach

Outline the solution as ordered phases. Each task should be small enough to implement and test on
its own, framed as a TDD slice (failing test → minimal code → cleanup).

### Phase 1 — <setup / scaffolding>
- [ ] <action and expected outcome>

### Phase 2 — <core implementation>
- [ ] <action>

### Phase 3 — <tests & docs>
- [ ] <action>

## Files

- **Create:** `path/to/new_file` — <purpose>
- **Change:** `path/to/existing_file` — <what changes and why>
- **Tests:** `path/to/test_file` — <what it covers>

## Test strategy

- **Unit:** <key scenarios and edge cases; target >80% on new code>
- **Integration:** <cross-component flow to verify>
- **Manual (if any):** <what to check by hand>

## Estimated monthly cloud cost

> Required whenever the feature adds or changes cloud (AWS) resources. Omit only for pure
> code/docs changes with no infrastructure impact.

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| <service> | <unit price> | <usage assumption> | ~$<x> |
| **Total (new AWS components)** | | | **~$<x>/month** |

- **Cost drivers & cheaper alternatives:** <what dominates the bill and how to reduce it>
- **External / non-AWS costs:** <e.g. third-party SaaS, billed separately>
- **Budget check:** <within the project's monthly target? yes/no>

## Success criteria

- [ ] <measurable outcome 1>
- [ ] <measurable outcome 2>
- [ ] Tests pass and coverage holds
- [ ] Follows project conventions (type hints, docstrings, naming)
- [ ] Docs updated

## Open questions & risks

- **Question:** <decision still needed?>
- **Risk:** <what could go wrong> — *Mitigation:* <how we reduce it>
- **Assumption:** <state assumptions so they can be checked>

## Progress log

- **YYYY-MM-DD** — <note about progress, blockers, or decisions>
