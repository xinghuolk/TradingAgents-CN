import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_unpublished_extractor_uses_an_installer_portable_source() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    extractor = next(
        dependency
        for dependency in pyproject["project"]["dependencies"]
        if dependency.startswith("financial-report-llm-extractor")
    )

    assert re.fullmatch(
        r"financial-report-llm-extractor @ "
        r"git\+https://github\.com/xinghuolk/financial-report-llm-extractor\.git@"
        r"[0-9a-f]{40}",
        extractor,
    )
    assert "financial-report-llm-extractor" not in (
        pyproject.get("tool", {}).get("uv", {}).get("sources", {})
    )
