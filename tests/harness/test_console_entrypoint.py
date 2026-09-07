import subprocess
import sys
import tomllib
from pathlib import Path

from scripts.harness import validate_console_entrypoint


ROOT = Path(__file__).resolve().parents[2]


def test_console_entrypoint_resolves_to_packaged_function() -> None:
    assert validate_console_entrypoint(ROOT) == []


def test_console_entrypoint_load_is_lazy() -> None:
    script = """
import builtins
import sys
import tomllib
from importlib.metadata import EntryPoint
from pathlib import Path

root = Path(sys.argv[1])
with (root / "pyproject.toml").open("rb") as handle:
    target = tomllib.load(handle)["project"]["scripts"]["tradingagents"]

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "typer" or name.startswith("typer."):
        raise AssertionError("loading console metadata initialized the CLI runtime")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
entrypoint = EntryPoint(name="tradingagents", value=target, group="console_scripts")
assert callable(entrypoint.load())
assert "typer" not in sys.modules
assert not any(
    name == "tradingagents" or name.startswith("tradingagents.")
    for name in sys.modules
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_cli_declares_typer_as_a_direct_dependency() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]

    assert any(dependency.startswith("typer") for dependency in dependencies)
