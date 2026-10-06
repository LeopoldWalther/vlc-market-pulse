#!/usr/bin/env python3
"""Consistency checker for the Architect / Review / Implement planning artifacts.

Run before opening a PR (and in CI) to keep three things in sync:

1. No stale references to old folder layouts have crept into ``.github/`` or ``dev/``.
2. Every real technical plan declares a valid CI gate set and points at an existing review.
3. The status shown in a feature's plan file and in ``dev/plans/README.md`` matches the progress
   recorded in its technical plan (``done`` tasks vs. total).

Exit code is non-zero when any hard error is found; warnings alone do not fail the run.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANS_DIR = ROOT / "dev" / "plans"
TECHNICAL_DIR = PLANS_DIR / "technical"
PLANS_README = PLANS_DIR / "README.md"

# CI checks a technical plan is allowed to require.
ALLOWED_CHECKS = {
    "python-lint-and-test",
    "node-test",
    "terraform-validate",
    "workflow-consistency",
}

# Workflow status markers, keyed by their canonical name.
STATUS_MARKERS = {
    "planned": "🔵",
    "in-progress": "🟡",
    "complete": "🟢",
    "blocked": "🔴",
}

# Folder layouts we have since moved away from; flag any lingering mention.
STALE_PATH_PATTERNS = [
    re.compile(r"\bdev/plan/"),
    re.compile(r"\bdev/plan\b"),
    re.compile(r"\bdev/implementations/"),
    re.compile(r"\.github/agents/"),
]

TEXT_SUFFIXES = {".md", ".txt", ".yml", ".yaml"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _looks_like_text(path: Path) -> bool:
    return path.name == ".gitkeep" or path.suffix.lower() in TEXT_SUFFIXES


def _marker_in(text: str) -> str | None:
    """Return the first known status marker found in ``text``, if any."""
    return next((m for m in STATUS_MARKERS.values() if m in text), None)


@dataclass
class PlanProgress:
    """Parsed state of a single technical plan needed for cross-checks."""

    path: Path
    feature_id: str
    task_count: int
    done_count: int
    declared_total: int | None
    required_checks: list[str]
    reviewed_plan: str | None


def _readme_statuses() -> dict[str, str]:
    """Map each ``FEATURE-XXX`` row in the plans README to its status marker."""
    result: dict[str, str] = {}
    for line in _read(PLANS_README).splitlines():
        if not line.startswith("| FEATURE-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        marker = _marker_in(cells[2])
        if marker:
            result[cells[0]] = marker
    return result


def _plan_file_status(plan_path: Path) -> str | None:
    """Read the ``**Status:**`` marker from a top-level feature plan file."""
    match = re.search(r"^\*\*Status:\*\*\s*(.+?)\s*$", _read(plan_path), flags=re.MULTILINE)
    return _marker_in(match.group(1)) if match else None


def _required_checks(text: str) -> list[str]:
    """Collect the list items under the ``required_checks:`` key (4-space indent)."""
    checks: list[str] = []
    inside = False
    for line in text.splitlines():
        if re.match(r"^\s{4}required_checks:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\s{4}[A-Za-z_]+:\s*", line):
            break
        if inside:
            item = re.match(r'^\s{6}-\s+"([^"]+)"\s*$', line)
            if item:
                checks.append(item.group(1))
    return checks


def _parse_plan(path: Path) -> PlanProgress | None:
    """Parse a technical plan, or return ``None`` for generic example files."""
    text = _read(path)

    feature_match = re.search(r'^\s{4}for_feature:\s+"([^"]+)"\s*$', text, flags=re.MULTILINE)
    if not feature_match:
        return None
    feature_id = feature_match.group(1)

    # Example plans use a non-canonical id (e.g. "FEATURE-001-example"); skip them.
    if not re.fullmatch(r"FEATURE-\d+", feature_id):
        return None

    reviewed = re.search(r'^\s{4}reviewed_plan:\s+"([^"]+)"\s*$', text, flags=re.MULTILINE)
    total = re.search(r"^\s{4}total_tasks:\s+(\d+)\s*$", text, flags=re.MULTILINE)
    tasks = re.findall(r'^\s{4}- id:\s+"[^"]+"\s*$', text, flags=re.MULTILINE)
    statuses = re.findall(r'^\s{6}status:\s+"([^"]+)"\s*$', text, flags=re.MULTILINE)

    return PlanProgress(
        path=path,
        feature_id=feature_id,
        task_count=len(tasks),
        done_count=sum(s == "done" for s in statuses),
        declared_total=int(total.group(1)) if total else None,
        required_checks=_required_checks(text),
        reviewed_plan=reviewed.group(1) if reviewed else None,
    )


def _expected_marker(done: int, total: int) -> str:
    if total <= 0 or done == 0:
        return STATUS_MARKERS["planned"]
    if done >= total:
        return STATUS_MARKERS["complete"]
    return STATUS_MARKERS["in-progress"]


def _plan_file_for(feature_id: str) -> Path | None:
    matches = sorted(PLANS_DIR.glob(f"{feature_id}-*.md"))
    return matches[0] if matches else None


def _check_stale_paths(errors: list[str]) -> None:
    for scope in (ROOT / ".github", ROOT / ".claude", ROOT / "dev"):
        for path in scope.rglob("*"):
            if not path.is_file() or not _looks_like_text(path):
                continue
            content = _read(path)
            for pattern in STALE_PATH_PATTERNS:
                if pattern.search(content):
                    rel = path.relative_to(ROOT).as_posix()
                    errors.append(f"Stale path '{pattern.pattern}' referenced in {rel}")
                    break


def _check_plan(plan: PlanProgress, readme: dict[str, str], errors: list[str], warnings: list[str]) -> None:
    rel = plan.path.relative_to(ROOT).as_posix()

    if plan.declared_total is not None and plan.declared_total != plan.task_count:
        errors.append(
            f"{rel}: total_tasks={plan.declared_total} but {plan.task_count} tasks are defined"
        )

    if not plan.required_checks:
        errors.append(f"{rel}: validation.required_checks is empty")
    for check in plan.required_checks:
        if check not in ALLOWED_CHECKS:
            errors.append(f"{rel}: unknown required_check '{check}' (allowed: {sorted(ALLOWED_CHECKS)})")

    if plan.reviewed_plan and not (ROOT / plan.reviewed_plan).exists():
        errors.append(f"{rel}: reviewed_plan path does not exist: {plan.reviewed_plan}")

    expected = _expected_marker(plan.done_count, plan.task_count)
    progress = f"{plan.done_count}/{plan.task_count}"

    plan_file = _plan_file_for(plan.feature_id)
    if plan_file is None:
        errors.append(f"{rel}: no top-level plan file '{plan.feature_id}-*.md' found in dev/plans/")
    else:
        rel_plan = plan_file.relative_to(ROOT).as_posix()
        marker = _plan_file_status(plan_file)
        if marker is None:
            errors.append(f"{rel_plan}: missing or unrecognized **Status:** header")
        elif marker != expected:
            errors.append(
                f"{rel_plan}: status {marker} disagrees with technical progress {progress} (expected {expected})"
            )

    readme_marker = readme.get(plan.feature_id)
    if readme_marker is None:
        warnings.append(f"dev/plans/README.md: no table row for {plan.feature_id}")
    elif readme_marker != expected:
        errors.append(
            f"dev/plans/README.md: status {readme_marker} for {plan.feature_id} "
            f"disagrees with technical progress {progress} (expected {expected})"
        )


def run() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    _check_stale_paths(errors)

    readme = _readme_statuses()
    for plan_path in sorted(TECHNICAL_DIR.glob("FEATURE-*-technical-plan.yaml")):
        plan = _parse_plan(plan_path)
        if plan is not None:
            _check_plan(plan, readme, errors, warnings)

    return errors, warnings


def main() -> int:
    errors, warnings = run()

    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")

    if errors:
        print(f"\nWorkflow consistency check failed with {len(errors)} error(s).")
        return 1

    print("Workflow consistency check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
