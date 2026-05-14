import os
import sys

# 将项目根目录加入 sys.path，确保 `import tradingagents` 可用
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 兼容性: `toml` 包未安装时用 `tomli` 作为替代 (提供相同 API)
try:
    import toml  # noqa: F401
except ModuleNotFoundError:
    try:
        import tomli as toml  # type: ignore[no-redef]
        sys.modules["toml"] = toml
    except ModuleNotFoundError:
        pass

# ----- begin PR-2 added fixture -----
import sys
import types
from unittest.mock import MagicMock
import pytest as _pytest


@_pytest.fixture(scope="session")
def stub_optional_llm_deps():
    """Stub heavy optional LLM deps for tests that import trading_graph.

    Tests that exercise the import chain through trading_graph.py (which
    transitively pulls in tradingagents.agents.utils.memory which imports
    `dashscope` and `chromadb` at module load time) but don't need real
    LLM behavior. Opt-in: request the fixture explicitly or wrap with an
    autouse fixture in the test file.
    """
    if "dashscope" not in sys.modules:
        m = types.ModuleType("dashscope")
        m.TextEmbedding = MagicMock()  # type: ignore[attr-defined]
        sys.modules["dashscope"] = m
    if "chromadb" not in sys.modules:
        sys.modules["chromadb"] = types.ModuleType("chromadb")
    if "chromadb.config" not in sys.modules:
        cc = types.ModuleType("chromadb.config")
        cc.Settings = MagicMock()  # type: ignore[attr-defined]
        sys.modules["chromadb.config"] = cc
    yield
    # Don't tear down — same process, leaving stubs in place for other tests
    # is safer than ripping them out (other tests may have cached imports
    # against them).
# ----- end PR-2 added fixture -----
