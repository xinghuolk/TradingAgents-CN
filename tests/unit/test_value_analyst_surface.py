from pathlib import Path

from cli.models import AnalystType
from cli.utils import ANALYST_ORDER


def test_cli_value_analyst_is_selectable():
    assert AnalystType.VALUE.value == "value"
    assert ("价值投资分析师 | Value Analyst", AnalystType.VALUE) in ANALYST_ORDER


def test_frontend_value_analyst_constant_is_mapped():
    constants = Path("frontend/src/constants/analysts.ts").read_text(encoding="utf-8")

    assert "id: 'value'" in constants
    assert "name: '价值投资分析师'" in constants
    assert "'价值投资分析师': 'value'" in constants
    assert "DEFAULT_ANALYSTS = ['市场分析师', '基本面分析师']" in constants
