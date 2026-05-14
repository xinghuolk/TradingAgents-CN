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

