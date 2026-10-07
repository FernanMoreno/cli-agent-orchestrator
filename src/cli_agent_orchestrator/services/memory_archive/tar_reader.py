"""Bounded tar transport for the existing untrusted-memory import pipeline."""

import gzip
import os
import stat
import tarfile
import tempfile
from contextlib import contextmanager
from io import RawIOBase
from pathlib import Path, PurePosixPath
from typing import Iterator


class _BoundedReader(RawIOBase):
    def __init__(self, stream: gzip.GzipFile, maximum: int) -> None:
        self.stream, self.remaining = stream, maximum

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.remaining + 1
        value = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(value)
        if self.remaining < 0:
            raise ValueError("Archive decompression limit exceeded")
        return value


@contextmanager
def extracted_bundle(source: Path) -> Iterator[Path]:
    """Never extract links, devices, traversal, duplicates or unconstrained members."""
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as compressed:
        info = os.fstat(compressed.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
            raise ValueError("Archive compressed size limit exceeded")
        with tempfile.TemporaryDirectory(prefix="cao-memory-import-") as temp:
            root = Path(temp)
            seen: set[str] = set()
            total = 0
            try:
                with gzip.GzipFile(fileobj=compressed) as expanded:
                    with tarfile.open(
                        fileobj=_BoundedReader(expanded, 128 * 1024 * 1024), mode="r|"
                    ) as archive:
                        for member in archive:
                            name = PurePosixPath(member.name)
                            if (
                                name.is_absolute()
                                or ".." in name.parts
                                or not name.parts
                                or len(name.parts) > 16
                                or "\\" in member.name
                                or len(member.name) > 1024
                            ):
                                raise ValueError("Unsafe archive member")
                            canonical = name.as_posix()
                            if canonical in seen or len(seen) >= 2048:
                                raise ValueError("Archive duplicate or member limit")
                            seen.add(canonical)
                            destination = root.joinpath(*name.parts)
                            if member.isdir():
                                destination.mkdir(parents=True, exist_ok=True)
                                continue
                            if (
                                not member.isfile()
                                or member.size < 0
                                or member.size > 4 * 1024 * 1024
                            ):
                                raise ValueError("Unsupported archive member or size")
                            total += member.size
                            if total > 64 * 1024 * 1024:
                                raise ValueError("Archive uncompressed size limit")
                            if destination.exists():
                                raise ValueError("Archive member collision")
                            stream = archive.extractfile(member)
                            if stream is None:
                                raise ValueError("Archive member unavailable")
                            content = stream.read(member.size + 1)
                            if len(content) != member.size:
                                raise ValueError("Archive truncated member")
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            with destination.open("xb") as output:
                                output.write(content)
                yield root
            except (tarfile.TarError, OSError, EOFError) as error:
                raise ValueError("Invalid memory tar archive") from error
