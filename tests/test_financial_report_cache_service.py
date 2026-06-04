"""财报缓存清理 service 单测。"""
from pathlib import Path

import pytest

from app.services.financial_report_cache_service import (
    _purge_directory_contents,
    purge_extractor_cache,
    purge_downloaded_pdfs,
)


def _make_tree(root: Path):
    """在 root 下造 2 个顶层文件 + 1 个含 2 个文件的子目录。"""
    (root / "a.json").write_text("aaaa", encoding="utf-8")        # 4 bytes
    (root / "b.pdf").write_bytes(b"123456")                       # 6 bytes
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("cc", encoding="utf-8")            # 2 bytes
    (sub / "d.txt").write_text("ddd", encoding="utf-8")           # 3 bytes
    # 总计 4 个文件，15 bytes


def test_purge_counts_and_deletes(tmp_path):
    _make_tree(tmp_path)
    result = _purge_directory_contents(str(tmp_path))
    assert result["deleted_files"] == 4
    assert result["freed_bytes"] == 15
    assert result["root"] == str(tmp_path)
    assert tmp_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_purge_empty_dir(tmp_path):
    result = _purge_directory_contents(str(tmp_path))
    assert result == {"deleted_files": 0, "freed_bytes": 0, "root": str(tmp_path)}


def test_purge_missing_dir(tmp_path):
    missing = tmp_path / "nope"
    result = _purge_directory_contents(str(missing))
    assert result == {"deleted_files": 0, "freed_bytes": 0, "root": str(missing)}


def test_purge_empty_root_string():
    result = _purge_directory_contents("")
    assert result == {"deleted_files": 0, "freed_bytes": 0, "root": ""}


def test_purge_rejects_filesystem_root():
    with pytest.raises(ValueError):
        _purge_directory_contents("/")


def test_purge_rejects_relative_path():
    with pytest.raises(ValueError):
        _purge_directory_contents("relative/dir")


def test_purge_rejects_home(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    with pytest.raises(ValueError):
        _purge_directory_contents(str(fake_home))


def test_purge_extractor_cache_uses_container_path(monkeypatch, tmp_path):
    container_root = tmp_path / "extractor"
    cache_dir = container_root / "tmp" / ".cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "x.json").write_text("x", encoding="utf-8")
    monkeypatch.setenv("FINANCIAL_REPORT_EXTRACTOR_CONTAINER_ROOT", str(container_root))
    result = purge_extractor_cache()
    assert result["deleted_files"] == 1
    assert result["root"] == str(cache_dir)
    assert list(cache_dir.iterdir()) == []


def test_purge_downloaded_pdfs_uses_container_path(monkeypatch, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "y.pdf").write_bytes(b"abc")
    monkeypatch.setenv("FINANCIAL_REPORT_PDF_CONTAINER_ROOT", str(pdf_dir))
    result = purge_downloaded_pdfs()
    assert result["deleted_files"] == 1
    assert result["root"] == str(pdf_dir)
    assert list(pdf_dir.iterdir()) == []


def test_purge_symlink_to_dir_not_followed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_text("keep me", encoding="utf-8")
    purge_root = tmp_path / "purge_me"
    purge_root.mkdir()
    link = purge_root / "link"
    link.symlink_to(target)
    result = _purge_directory_contents(str(purge_root))
    assert result["deleted_files"] == 1          # 仅符号链接本身
    assert link.exists() is False                # 链接被删除
    assert (target / "secret.txt").exists()      # 目标目录未被触碰
