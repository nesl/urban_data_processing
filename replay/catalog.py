"""Unified, read-only access to local date folders and daily TAR archives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import io
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Iterator

from .model import FileReference


DATE_RE = re.compile(r"\d{8}")


@dataclass(frozen=True)
class StoredFile:
    path: Path
    relative_path: str
    member: str | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.relative_path).name

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.relative_path).suffix.lower()

    def open(self) -> io.BufferedReader | io.BytesIO:
        if self.member is None:
            return self.path.open("rb")
        with tarfile.open(self.path, "r:*") as archive:
            handle = archive.extractfile(self.member)
            if handle is None:
                raise FileNotFoundError(f"Archive member is not a file: {self.member}")
            return io.BytesIO(handle.read())

    def read_bytes(self) -> bytes:
        with self.open() as handle:
            return handle.read()

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding, errors="replace")

    def reference(self, media_type: str) -> dict[str, str | None]:
        return {"path": str(self.path), "member": self.member, "media_type": media_type}


@dataclass(frozen=True)
class Partition:
    source_folder: str
    day: str
    root: Path
    archive: bool

    def files(self) -> Iterator[StoredFile]:
        if not self.archive:
            for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
                yield StoredFile(path, path.relative_to(self.root).as_posix())
            return
        with tarfile.open(self.root, "r:*") as archive:
            for member in sorted((m for m in archive.getmembers() if m.isfile()), key=lambda m: m.name):
                parts = list(PurePosixPath(member.name).parts)
                while parts and (parts[0] == self.source_folder or parts[0] == self.day):
                    parts.pop(0)
                relative = PurePosixPath(*parts).as_posix() if parts else PurePosixPath(member.name).name
                yield StoredFile(self.root, relative, member.name)


class DataCatalog:
    """Resolve source/date partitions, preferring current local data over TARs."""

    def __init__(self, data_root: str | Path, backup_root: str | Path):
        self.data_root = Path(data_root)
        backup = Path(backup_root)
        self.archive_roots = (backup / "raw", backup)

    def dates(self, source_folder: str) -> list[str]:
        found: set[str] = set()
        local = self.data_root / source_folder
        if local.is_dir():
            found.update(p.name for p in local.iterdir() if p.is_dir() and DATE_RE.fullmatch(p.name))
        for root in self.archive_roots:
            archived = root / source_folder
            if archived.is_dir():
                found.update(p.stem for p in archived.glob("*.tar") if DATE_RE.fullmatch(p.stem))
        return sorted(found)

    def partition(self, source_folder: str, day: str) -> Partition | None:
        local = self.data_root / source_folder / day
        if local.is_dir():
            return Partition(source_folder, day, local, False)
        for root in self.archive_roots:
            archive = root / source_folder / f"{day}.tar"
            if archive.is_file():
                return Partition(source_folder, day, archive, True)
        return None

    def select_dates(self, source_folders: list[str], start: date, end: date) -> list[str]:
        return sorted({day for source in source_folders for day in self.dates(source)
                       if start <= datetime.strptime(day, "%Y%m%d").date() < end})


def open_file_reference(reference: FileReference) -> io.BufferedReader | io.BytesIO:
    """Open a local or archived asset referenced by a common observation."""
    path = Path(reference.path)
    if reference.member is None:
        return path.open("rb")
    with tarfile.open(path, "r:*") as archive:
        handle = archive.extractfile(reference.member)
        if handle is None:
            raise FileNotFoundError(f"Archive member is not a file: {reference.member}")
        return io.BytesIO(handle.read())
