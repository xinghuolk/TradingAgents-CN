import os
import stat
from hashlib import sha256

import pytest

from app.services.real_portfolio.archive import archive_portfolio_bytes
from app.services.real_portfolio.errors import PortfolioError
from tests.unit.real_portfolio.fixtures import snapshot_bytes


def test_archive_uses_private_server_components_and_exact_bytes(tmp_path):
    content = snapshot_bytes()
    result = archive_portfolio_bytes(tmp_path, "user@example.com", content)
    assert result.created is True
    assert result.path.read_bytes() == content
    assert result.path == (
        tmp_path
        / "private"
        / "real_portfolio"
        / sha256(b"user@example.com").hexdigest()
        / "main"
        / f"{sha256(content).hexdigest()}.xls"
    )
    if os.name == "posix":
        assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
        for directory in (result.path.parent, *result.path.parents[1:4]):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_existing_archive_is_verified_without_replacement(tmp_path):
    content = snapshot_bytes()
    first = archive_portfolio_bytes(tmp_path, "user-1", content)
    inode = first.path.stat().st_ino
    second = archive_portfolio_bytes(tmp_path, "user-1", content)
    assert not second.created
    assert second.path.stat().st_ino == inode
    first.path.write_bytes(b"different")
    with pytest.raises(PortfolioError) as captured:
        archive_portfolio_bytes(tmp_path, "user-1", content)
    assert captured.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
    assert first.path.read_bytes() == b"different"


@pytest.mark.parametrize("kind", ["ancestor", "file", "directory"])
def test_archive_rejects_symlinks_and_non_regular_files(tmp_path, kind):
    content = snapshot_bytes()
    first = archive_portfolio_bytes(tmp_path, "user-1", content)
    first.path.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "ancestor":
        first.path.parent.rmdir()
        first.path.parent.symlink_to(outside, target_is_directory=True)
    elif kind == "file":
        target = outside / "source"
        target.write_bytes(content)
        first.path.symlink_to(target)
    else:
        first.path.mkdir()
    with pytest.raises(PortfolioError) as captured:
        archive_portfolio_bytes(tmp_path, "user-1", content)
    assert captured.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
    assert not (outside / first.path.name).exists()


def test_failed_new_write_removes_only_new_archive(tmp_path, monkeypatch):
    existing = archive_portfolio_bytes(tmp_path, "user-1", b"existing")

    def fail_write(*args):
        raise OSError("injected disk failure")

    monkeypatch.setattr(os, "write", fail_write)
    with pytest.raises(PortfolioError):
        archive_portfolio_bytes(tmp_path, "user-1", b"new")
    assert list(tmp_path.rglob("*.xls")) == [existing.path]
    assert existing.path.read_bytes() == b"existing"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_existing_private_archive_permissions_are_restricted(tmp_path):
    result = archive_portfolio_bytes(tmp_path, "user-1", b"exact")
    result.path.chmod(0o644)
    private = tmp_path / "private"
    private.chmod(0o755)
    reused = archive_portfolio_bytes(tmp_path, "user-1", b"exact")
    assert not reused.created
    assert stat.S_IMODE(reused.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


def test_self_referential_archive_symlink_has_safe_error(tmp_path):
    result = archive_portfolio_bytes(tmp_path, "user-1", b"exact")
    result.path.unlink()
    result.path.symlink_to(result.path.name)
    with pytest.raises(PortfolioError) as captured:
        archive_portfolio_bytes(tmp_path, "user-1", b"exact")
    assert captured.value.code == "PORTFOLIO_STORAGE_UNAVAILABLE"
    assert str(tmp_path) not in str(captured.value)
    assert result.path.is_symlink()
