"""Private, exact-byte source archives with server-owned path components."""

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.services.real_portfolio.errors import PortfolioError


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    path: Path
    created: bool


def archive_portfolio_bytes(root: Path, user_id: str, content: bytes) -> ArchiveResult:
    private = Path(root) / "private"
    handles: list[int] = []
    created = False
    filename = f"{sha256(content).hexdigest()}.xls"
    try:
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        private = private.resolve(strict=True)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(private, directory_flags)
        handles.append(directory)
        os.fchmod(directory, 0o700)
        path = private
        # Descriptor-relative traversal prevents an ancestor being swapped to a symlink.
        for component in (
            "real_portfolio",
            sha256(user_id.encode("utf-8")).hexdigest(),
            "main",
        ):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open(component, directory_flags, dir_fd=directory)
            handles.append(directory)
            os.fchmod(directory, 0o700)
            path = path / component
        path = path / filename
        if not path.resolve().is_relative_to(private):
            raise OSError("archive escaped private root")
        try:
            descriptor = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("archive is not a regular file")
            if created:
                remaining = memoryview(content)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written == 0:
                        raise OSError("incomplete archive write")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            else:
                with os.fdopen(os.dup(descriptor), "rb") as existing:
                    if existing.read(len(content) + 1) != content:
                        raise OSError("existing archive does not match")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return ArchiveResult(path, created)
    except OSError:
        if created:
            try:
                os.unlink(filename, dir_fd=directory)
            except OSError:
                pass
        raise PortfolioError(
            "PORTFOLIO_STORAGE_UNAVAILABLE", "private portfolio archive is unavailable"
        ) from None
    finally:
        for descriptor in reversed(handles):
            os.close(descriptor)
