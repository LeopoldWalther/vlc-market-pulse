# Review — FEATURE-XXX: <feature title>

**Reviewer:** `/reviewer` · **Date:** YYYY-MM-DD · **Plan:** [FEATURE-XXX](../plans/FEATURE-XXX-<slug>.md)
**Verdict:** <✅ Approved | ⚠️ Changes Recommended | 🔄 Alternative Proposed | ❌ Major Revision Needed>

## Summary

Two or three sentences: the overall read on the plan and the headline finding(s).

## Strengths

- ✅ <what the plan gets right>
- ✅ <another strength>

## Findings

Each finding gets an ID, a severity, and a concrete recommendation. Severity drives whether it
blocks: 🔴 must-fix, 🟡 should-fix, 🟢 optional.

### 🔴 H1 — <title>

- **Problem:** <what's wrong>
- **Impact:** <what happens if ignored>
- **Recommendation:** <specific, actionable fix>
- **Evidence:** <code/doc reference or prior experience>

### 🟡 M1 — <title>

- **Problem:** <description>
- **Recommendation:** <suggested improvement>
- **Effort:** <S/M/L>

### 🟢 L1 — <title>

- **Suggestion:** <nice-to-have improvement>
- **Why:** <benefit> — safe to skip without blocking.

## Alternatives considered

- **<approach name>** — <how it differs>. Trade-off: <pros vs. cons>. Verdict: <use when… / stick
  with the plan because…>.

## Risks

| Risk | Likelihood | Impact | Severity | Mitigation |
| --- | --- | --- | --- | --- |
| <description> | Low/Med/High | Low/Med/High | 🔴/🟡/🟢 | <how to mitigate> |

## Effort check

- **Plan estimate:** <S/M/L (~Xh)>
- **Reviewer estimate:** <S/M/L (~Yh)> — confidence <Low/Med/High>
- **Why it differs / hidden complexity:** <factors that move the number>

## Reuse & conflicts

- **Reuse:** `path/to/module` — <already provides…>
- **Conflict / coordinate with:** <component or in-flight work>

## Approval criteria

- **Blockers (must fix):** <list of 🔴 items>
- **Recommended:** <list of 🟡 items>
- **Optional:** <list of 🟢 items>

## Next step

<e.g. "Address H1–H2, then `/implementer FEATURE-XXX`" or "Return to `/architect` to
rework the data model.">

---

### Post-implementation notes
*Filled in after the task ships.*

- **Worked well:** <…>
- **Missed in review:** <…>
- **Estimated vs. actual:** <X> vs. <Y>
