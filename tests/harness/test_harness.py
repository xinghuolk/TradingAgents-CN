import sys
from pathlib import Path

from scripts.harness import (
    Check,
    run_checks,
    validate_local_links,
    validate_workflows,
)


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
        "jobs:\n  quality:\n    steps:\n      - run: python scripts/harness.py\n"
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
