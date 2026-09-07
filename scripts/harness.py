#!/usr/bin/env python3
"""Run the small, deterministic validation loop used locally and in CI."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOCUMENTS = (
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("ARCHITECTURE.md"),
    Path("docs/README.md"),
    Path("docs/development.md"),
    Path("docs/testing.md"),
    Path("docs/technical-debt.md"),
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


@dataclass(frozen=True)
class Check:
    name: str
    command: tuple[str, ...]
    cwd: Path
    env: dict[str, str] | None = None


def validate_local_links(document: Path, root: Path) -> list[str]:
    """Return missing relative-link errors for one Markdown document."""
    errors: list[str] = []
    relative_document = document.relative_to(root)
    text = document.read_text(encoding="utf-8")

    for match in MARKDOWN_LINK.finditer(text):
        raw_target = match.group(1).strip().strip("<>")
        target_without_fragment = raw_target.split("#", 1)[0]
        if not target_without_fragment:
            continue
        if "://" in target_without_fragment or target_without_fragment.startswith(
            ("mailto:", "/")
        ):
            continue

        target = document.parent / unquote(target_without_fragment)
        if not target.exists():
            errors.append(
                f"{relative_document}: missing local link target {raw_target}"
            )

    return errors


def validate_repository(root: Path) -> list[str]:
    """Validate the small repository knowledge map, not historical archives."""
    errors: list[str] = []
    for relative_path in CANONICAL_DOCUMENTS:
        document = root / relative_path
        if not document.is_file():
            errors.append(f"{relative_path}: required repository document is missing")
            continue
        errors.extend(validate_local_links(document, root))
    return errors


def _vite_executable(root: Path) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return root / "frontend" / "node_modules" / ".bin" / f"vite{suffix}"


def build_checks(root: Path, temp_dir: Path) -> list[Check]:
    """Build the ordered command list for the full quick gate."""
    isolated_env = {
        "PYTHONPYCACHEPREFIX": str(temp_dir / "pycache"),
        "TRADINGAGENTS_LOG_DIR": str(temp_dir / "logs"),
    }
    return [
        Check(
            name="Python source compilation",
            command=(
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "app",
                "tradingagents",
                "cli",
            ),
            cwd=root,
            env=isolated_env,
        ),
        Check(
            name="Service-free pytest suite",
            command=(
                sys.executable,
                "-m",
                "pytest",
                "-c",
                "tests/pytest.ini",
                "tests/config",
                "tests/unit/real_portfolio",
                "tests/harness",
                "-q",
            ),
            cwd=root,
            env=isolated_env,
        ),
        Check(
            name="Frontend production bundle",
            command=(str(_vite_executable(root)), "build"),
            cwd=root / "frontend",
        ),
    ]


def run_checks(checks: list[Check]) -> int:
    """Run checks in order and stop at the first failure."""
    for check in checks:
        print(f"\n==> {check.name}", flush=True)
        print(f"    {shlex.join(check.command)}", flush=True)
        environment = os.environ.copy()
        if check.env:
            environment.update(check.env)
        result = subprocess.run(check.command, cwd=check.cwd, env=environment, check=False)
        if result.returncode:
            print(f"FAILED: {check.name} (exit {result.returncode})", file=sys.stderr)
            return result.returncode
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--structural-only",
        action="store_true",
        help="validate only the canonical repository map",
    )
    mode.add_argument(
        "--python-only",
        action="store_true",
        help="validate the repository map, Python compilation, and quick tests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_repository(ROOT)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Repository structure: OK")

    if args.structural_only:
        return 0

    with tempfile.TemporaryDirectory(prefix="tradingagents-harness-") as temp:
        checks = build_checks(ROOT, Path(temp))
        if args.python_only:
            checks = [check for check in checks if not check.name.startswith("Frontend")]
        result = run_checks(checks)

    if result == 0:
        print("\nHarness: PASS")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
