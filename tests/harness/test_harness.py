import os
import subprocess
import sys
from pathlib import Path

import scripts.harness as harness

from scripts.harness import (
    Check,
    run_checks,
    validate_local_links,
    validate_workflows,
)


ROOT = Path(__file__).resolve().parents[2]


def test_iter_current_documents_includes_current_tree(tmp_path: Path) -> None:
    current = tmp_path / "docs" / "current"
    current.mkdir(parents=True)
    (current / "getting-started.md").write_text("# Start\n", encoding="utf-8")
    (current / "nested").mkdir()
    (current / "nested" / "reference.md").write_text("# Ref\n", encoding="utf-8")

    relative = {
        path.relative_to(tmp_path)
        for path in harness.iter_current_documents(tmp_path)
        if path.exists()
    }

    assert Path("docs/current/getting-started.md") in relative
    assert Path("docs/current/nested/reference.md") in relative


def _workflow_step_script(workflow: Path, step_name: str) -> str:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    name_line = f"    - name: {step_name}"
    start = lines.index(name_line)
    run_line = next(
        index for index in range(start + 1, len(lines)) if lines[index] == "      run: |"
    )
    script_lines: list[str] = []
    for line in lines[run_line + 1 :]:
        if line and not line.startswith("        "):
            break
        script_lines.append(line[8:] if line else "")
    return "\n".join(script_lines)


def test_validate_local_links_reports_missing_target(tmp_path: Path) -> None:
    guide = tmp_path / "docs" / "guide.md"
    target = tmp_path / "ARCHITECTURE.md"
    guide.parent.mkdir()
    guide.write_text("Read the [architecture](../ARCHITECTURE.md).\n", encoding="utf-8")
    target.write_text("# Architecture\n", encoding="utf-8")

    assert validate_local_links(guide, tmp_path) == []

    target.unlink()

    assert validate_local_links(guide, tmp_path) == [
        "docs/guide.md: missing local link target ../ARCHITECTURE.md"
    ]


def test_run_checks_stops_after_first_failure(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    checks = [
        Check(
            name="expected failure",
            command=(sys.executable, "-c", "raise SystemExit(7)"),
            cwd=tmp_path,
        ),
        Check(
            name="must not run",
            command=(
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()",
            ),
            cwd=tmp_path,
        ),
    ]

    assert run_checks(checks) == 7
    assert not marker.exists()


def test_validate_workflows_rejects_direct_push_to_main(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "quality.yml").write_text(
        "jobs:\n  quality:\n    steps:\n      - run: python scripts/harness.py\n",
        encoding="utf-8",
    )
    (workflows / "docker-publish.yml").write_text(
        "jobs:\n  quality:\n    uses: ./.github/workflows/quality.yml\n"
        "  build-and-push:\n    needs: quality\n",
        encoding="utf-8",
    )
    upstream = workflows / "upstream-sync-check.yml"
    upstream.write_text("jobs:\n  check-upstream:\n", encoding="utf-8")

    assert validate_workflows(tmp_path) == []

    upstream.write_text(
        "jobs:\n  auto-sync:\n    steps:\n      - run: git push origin main\n",
        encoding="utf-8",
    )

    assert validate_workflows(tmp_path) == [
        ".github/workflows/upstream-sync-check.yml: direct push to main is forbidden"
    ]


def test_validate_workflows_rejects_indirect_push_to_main(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "quality.yml").write_text(
        "jobs:\n  quality:\n    steps:\n      - run: python scripts/harness.py\n",
        encoding="utf-8",
    )
    (workflows / "docker-publish.yml").write_text(
        "jobs:\n  quality:\n    uses: ./.github/workflows/quality.yml\n"
        "  build-and-push:\n    needs: quality\n",
        encoding="utf-8",
    )
    (workflows / "upstream-sync-check.yml").write_text(
        "jobs:\n  auto-sync:\n    steps:\n"
        "      - run: git push --force-with-lease origin HEAD:main\n",
        encoding="utf-8",
    )

    assert validate_workflows(tmp_path) == [
        ".github/workflows/upstream-sync-check.yml: direct push to main is forbidden"
    ]


def test_validate_workflows_rejects_output_interpolation_in_javascript(
    tmp_path: Path,
) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "quality.yml").write_text(
        "jobs:\n  quality:\n    steps:\n      - run: python scripts/harness.py\n",
        encoding="utf-8",
    )
    (workflows / "docker-publish.yml").write_text(
        "jobs:\n  quality:\n    uses: ./.github/workflows/quality.yml\n"
        "  build-and-push:\n    needs: quality\n",
        encoding="utf-8",
    )
    (workflows / "upstream-sync-check.yml").write_text(
        "jobs:\n  check-upstream:\n    steps:\n"
        "      - uses: actions/github-script@v7\n"
        "        with:\n"
        "          script: |\n"
        "            const recent = `${{ steps.check.outputs.recent }}`;\n",
        encoding="utf-8",
    )

    assert validate_workflows(tmp_path) == [
        ".github/workflows/upstream-sync-check.yml: step outputs must not be interpolated into JavaScript"
    ]


def test_upstream_analysis_handles_zero_category_matches(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "harness@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Harness Test"], cwd=tmp_path, check=True
    )
    marker = tmp_path / "marker"
    marker.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    marker.write_text("upstream\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "routine maintenance"], cwd=tmp_path, check=True)
    upstream = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    subprocess.run(["git", "reset", "--hard", "-q", base], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/upstream/main", upstream],
        cwd=tmp_path,
        check=True,
    )

    output = tmp_path / "github-output"
    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(output)
    script = _workflow_step_script(
        ROOT / ".github" / "workflows" / "upstream-sync-check.yml",
        "分析更新类型",
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        "features=0",
        "fixes=0",
        "docs=0",
        "priority=low",
        "reason=常规更新",
    ]
