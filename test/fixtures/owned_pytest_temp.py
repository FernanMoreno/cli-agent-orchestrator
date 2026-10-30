"""Short private pytest paths for native Unix IPC; artefacts remain for inspection."""

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _private_directory(path: Path):
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or path.resolve(strict=True) != path
    ):
        raise ValueError("pytest temporary directory must be private and owned")
    return info


@dataclass(frozen=True)
class OwnedPytestTemp:
    root: Path
    identity: tuple[int, int]

    @property
    def tmpdir(self) -> Path:
        return self.root / "t"

    @property
    def basetemp(self) -> Path:
        return self.tmpdir / "b"

    def metadata(self) -> dict:
        info = _private_directory(self.root)
        if (info.st_dev, info.st_ino) != self.identity:
            raise ValueError("pytest private root identity changed")
        _private_directory(self.tmpdir)
        _private_directory(self.basetemp)
        return {
            "schema": "caos.owned-pytest-temp.v1",
            "root": str(self.root),
            "tmpdir": str(self.tmpdir),
            "basetemp": str(self.basetemp),
            "device": info.st_dev,
            "inode": info.st_ino,
            "owner_uid": info.st_uid,
            "retained_for_inspection": True,
        }

    def configure(self, args: list[str], environment: dict[str, str]):
        if any(arg == "--basetemp" or arg.startswith("--basetemp=") for arg in args):
            raise ValueError("pytest basetemp must be assigned by its owner")
        self.metadata()
        return (
            [*args, "--basetemp", str(self.basetemp)],
            {**environment, "TMPDIR": str(self.tmpdir)},
        )


def create_owned_pytest_temp() -> OwnedPytestTemp:
    """Avoid ambient deep TMPDIR and reserve a fresh namespace per subprocess."""
    root = Path(tempfile.mkdtemp(prefix="cp", dir="/tmp"))
    root.chmod(0o700)
    info = _private_directory(root)
    value = OwnedPytestTemp(root, (info.st_dev, info.st_ino))
    value.tmpdir.mkdir(mode=0o700)
    value.basetemp.mkdir(mode=0o700)
    value.metadata()
    return value
