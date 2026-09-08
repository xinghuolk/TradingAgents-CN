#!/usr/bin/env python3
"""Run the small, deterministic validation loop used locally and in CI."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
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
ACTIVE_SUPPORT_DOCUMENTS = (
    Path("README.md"),
    Path(".github/ISSUE_TEMPLATE/question.md"),
    Path(".github/pull_request_template.md"),
)
ARCHIVE_POLICY = Path("docs/archive/README.md")
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


def iter_current_documents(root: Path) -> tuple[Path, ...]:
    documents = [root / path for path in CANONICAL_DOCUMENTS]
    documents.extend(root / path for path in ACTIVE_SUPPORT_DOCUMENTS)
    documents.append(root / ARCHIVE_POLICY)
    current_root = root / "docs" / "current"
    if current_root.is_dir():
        documents.extend(sorted(current_root.rglob("*.md")))
    return tuple(dict.fromkeys(documents))


def validate_repository(root: Path) -> list[str]:
    """Validate the small repository knowledge map, not historical archives."""
    errors: list[str] = []
    for document in iter_current_documents(root):
        relative_path = document.relative_to(root)
        if not document.is_file():
            errors.append(f"{relative_path}: required repository document is missing")
            continue
        errors.extend(validate_local_links(document, root))
    errors.extend(validate_workflows(root))
    errors.extend(validate_console_entrypoint(root))
    return errors


def validate_console_entrypoint(root: Path) -> list[str]:
    """Validate that the console target is a declared function in a packaged module."""
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return ["pyproject.toml: project metadata is missing"]

    with pyproject_path.open("rb") as handle:
        pyproject = tomllib.load(handle)
    target = pyproject.get("project", {}).get("scripts", {}).get("tradingagents")
    if not isinstance(target, str) or target.count(":") != 1:
        return ["pyproject.toml: tradingagents console target must be module:function"]

    module_name, function_name = target.split(":", 1)
    module_path = root.joinpath(*module_name.split(".")).with_suffix(".py")
    if not module_path.is_file():
        package_init = root.joinpath(*module_name.split("."), "__init__.py")
        if package_init.is_file():
            module_path = package_init
        else:
            return [f"pyproject.toml: console module {module_name} is missing"]

    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    declared_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if function_name not in declared_functions:
        return [
            f"pyproject.toml: console target {target} does not name a declared function"
        ]

    if "." in module_name:
        package_name = module_name.split(".", 1)[0]
        package_patterns = (
            pyproject.get("tool", {})
            .get("setuptools", {})
            .get("packages", {})
            .get("find", {})
            .get("include", [])
        )
        if not any(
            fnmatch.fnmatchcase(package_name, pattern) for pattern in package_patterns
        ):
            return [
                f"pyproject.toml: console package {package_name} is not included by setuptools"
            ]
    return []


def validate_workflows(root: Path) -> list[str]:
    """Check the small set of automation properties that protect repository state."""
    workflow_dir = root / ".github" / "workflows"
    required = ("quality.yml", "docker-publish.yml", "upstream-sync-check.yml")
    errors: list[str] = []
    contents: dict[str, str] = {}

    for name in required:
        path = workflow_dir / name
        if not path.is_file():
            errors.append(f".github/workflows/{name}: required workflow is missing")
            continue
        contents[name] = path.read_text(encoding="utf-8")

    quality = contents.get("quality.yml", "")
    if quality and "python scripts/harness.py" not in quality:
        errors.append(".github/workflows/quality.yml: harness command is missing")

    docker_publish = contents.get("docker-publish.yml", "")
    if docker_publish and "uses: ./.github/workflows/quality.yml" not in docker_publish:
        errors.append(
            ".github/workflows/docker-publish.yml: quality job must reuse quality.yml"
        )
    if docker_publish and not re.search(
        r"^\s+needs:\s*quality\s*$", docker_publish, re.MULTILINE
    ):
        errors.append(
            ".github/workflows/docker-publish.yml: publishing must need quality"
        )

    direct_main_push = re.compile(
        r"\bgit\s+push\b[^\n]*(?:(?<![\w/-])main(?![\w/-])|:main\b)"
    )
    for path in sorted(workflow_dir.glob("*.y*ml")):
        workflow = path.read_text(encoding="utf-8")
        if direct_main_push.search(workflow):
            errors.append(
                f"{path.relative_to(root)}: direct push to main is forbidden"
            )
        lines = workflow.splitlines()
        for index, line in enumerate(lines):
            if line.strip() != "script: |":
                continue
            indentation = len(line) - len(line.lstrip())
            script_lines: list[str] = []
            for script_line in lines[index + 1 :]:
                if script_line.strip():
                    script_indentation = len(script_line) - len(script_line.lstrip())
                    if script_indentation <= indentation:
                        break
                script_lines.append(script_line)
            script = "\n".join(script_lines)
            if re.search(r"\$\{\{\s*steps\.[^}]+\.outputs\.", script):
                errors.append(
                    f"{path.relative_to(root)}: step outputs must not be interpolated into JavaScript"
                )
                break
    return errors


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
                "tests/unit/stock_research",
                "tests/unit/test_stock_research_router.py",
                "tests/harness",
                "-q",
            ),
            cwd=root,
            env=isolated_env,
        ),
        Check(
            name="Frontend production bundle",
            command=("npm", "--prefix", "frontend", "run", "bundle"),
            cwd=root,
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
