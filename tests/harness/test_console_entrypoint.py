from pathlib import Path

from scripts.harness import validate_console_entrypoint


ROOT = Path(__file__).resolve().parents[2]


def test_console_entrypoint_resolves_to_packaged_function() -> None:
    assert validate_console_entrypoint(ROOT) == []
