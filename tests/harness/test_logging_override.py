import logging
from pathlib import Path

from tradingagents.utils.logging_manager import TradingAgentsLogger


ROOT = Path(__file__).resolve().parents[2]


def test_log_directory_environment_override_applies_to_toml(
    monkeypatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "logging.toml").write_text(
        (ROOT / "config" / "logging.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    override = tmp_path / "isolated-logs"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRADINGAGENTS_LOG_DIR", str(override))

    manager = TradingAgentsLogger()
    try:
        configured_directories = {
            manager.config["handlers"][name]["directory"]
            for name in ("file", "error", "structured")
        }
        assert configured_directories == {str(override)}
        assert (override / "tradingagents.log").is_file()
        assert (override / "error.log").is_file()
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
