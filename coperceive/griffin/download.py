# SPDX-License-Identifier: Apache-2.0
"""Download Griffin archives from ``datasets/wjh-svm/Griffin`` and extract them.

``md5.txt`` lists a digest of itself that does not match the file (measured
2026-10-15). That self-line is ignored. Every payload that is downloaded is
checked. A split archive (``*.z01``, ``*.z02``, …, ``*.zip``) is concatenated
in that order and opened as one zip. See ``docs/datasets/griffin.md``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from coperceive.griffin.catalog import (
    REPO_ID,
    REPO_TYPE,
    TOOLKIT_LICENSE_URL,
    archives_for,
    folder_for,
)
from coperceive.griffin.licence import (
    LicenceError,
    ack_path,
    acknowledgement_present,
    assert_mit,
    write_acknowledgement,
)

_PART = re.compile(r"^(?P<base>.+)\.z(?P<index>\d+)$")
_SIDES = ("vehicle-side", "drone-side")


class DiskSpaceError(RuntimeError):
    """Not enough free space for the selected archives."""


class ChecksumError(RuntimeError):
    """A downloaded file does not match ``md5.txt``."""


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int


@dataclass(frozen=True)
class PlannedFile:
    """One file that would be fetched. ``size`` is the Hub byte size."""

    path: str
    size: int
    archive: str | None
    role: str


@dataclass(frozen=True)
class DownloadReport:
    subset: str
    folder: str
    dest: Path
    files: tuple[PlannedFile, ...]
    required_free_bytes: int
    notice: str
    acknowledgement: str
    dry_run: bool
    self_md5_note: str
    downloaded: tuple[str, ...]
    skipped: tuple[str, ...]
    release_root: Path | None


class Hub:
    """The small surface the downloader needs. Tests pass a fake."""

    def list_files(self, folder: str) -> list[RemoteFile]:
        raise NotImplementedError

    def download(self, filename: str, dest: Path) -> None:
        raise NotImplementedError

    def read_dataset_card(self) -> str:
        raise NotImplementedError

    def read_toolkit_license(self) -> str:
        raise NotImplementedError


def resolve_hf_token(
    environ: Mapping[str, str] | None = None,
    keyring_get: Callable[[], str | None] | None = None,
) -> str | bool:
    """Return a token for ``huggingface_hub``, or ``False`` so no cached token is used.

    An ``HF_TOKEN`` already in the environment wins. Otherwise the keyring
    service ``huggingface`` / username ``HF_TOKEN`` is tried. The value is
    returned to the caller and is not written to the environment, argv, or a file.
    """
    env = os.environ if environ is None else environ
    value = env.get("HF_TOKEN", "")
    if value:
        return value
    getter = _keyring_password if keyring_get is None else keyring_get
    try:
        found = getter()
    except Exception:
        return False
    if found:
        return found
    return False


def _keyring_password() -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        return keyring.get_password("huggingface", "HF_TOKEN")
    except Exception:
        return None


class HfHub(Hub):
    """Live Hub client. ``token`` is passed only as the library argument."""

    def __init__(self, token: str | bool) -> None:
        self._token = token

    def list_files(self, folder: str) -> list[RemoteFile]:
        from huggingface_hub import HfApi

        api = HfApi()
        try:
            entries = api.list_repo_tree(
                REPO_ID,
                path_in_repo=f"datasets/{folder}",
                repo_type=REPO_TYPE,
                recursive=False,
                token=self._token,
            )
        except Exception as exc:
            raise RuntimeError(f"listing datasets/{folder} failed ({type(exc).__name__})") from None
        files: list[RemoteFile] = []
        for entry in entries:
            path = getattr(entry, "path", None)
            size = getattr(entry, "size", None)
            if not isinstance(path, str) or not isinstance(size, int):
                continue
            files.append(RemoteFile(path=path, size=_hub_size(entry)))
        return files

    def download(self, filename: str, dest: Path) -> None:
        from huggingface_hub import hf_hub_download

        try:
            local = hf_hub_download(
                repo_id=REPO_ID,
                filename=filename,
                repo_type=REPO_TYPE,
                token=self._token,
            )
        except Exception as exc:
            raise RuntimeError(f"download failed for {filename} ({type(exc).__name__})") from None
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_name(dest.name + ".partial")
        shutil.copyfile(local, temporary)
        os.replace(temporary, dest)

    def read_dataset_card(self) -> str:
        from huggingface_hub import hf_hub_download

        try:
            local = hf_hub_download(
                repo_id=REPO_ID,
                filename="README.md",
                repo_type=REPO_TYPE,
                token=self._token,
            )
        except Exception as exc:
            raise RuntimeError(f"dataset card download failed ({type(exc).__name__})") from None
        return Path(local).read_text(encoding="utf-8")

    def read_toolkit_license(self) -> str:
        import urllib.request

        try:
            with urllib.request.urlopen(TOOLKIT_LICENSE_URL, timeout=60) as response:
                return response.read().decode("utf-8")
        except Exception as exc:
            raise RuntimeError(f"toolkit licence download failed ({type(exc).__name__})") from None


def _hub_size(entry: object) -> int:
    lfs = getattr(entry, "lfs", None)
    lfs_size = getattr(lfs, "size", None) if lfs is not None else None
    if isinstance(lfs_size, int) and lfs_size > 0:
        return lfs_size
    size = getattr(entry, "size", None)
    if not isinstance(size, int):
        raise RuntimeError(f"Hub entry {getattr(entry, 'path', '?')} has no size")
    return size


def plan_files(
    entries: list[RemoteFile],
    folder: str,
    archives: tuple[str, ...],
) -> tuple[PlannedFile, ...]:
    """Select ``md5.txt`` and every part of ``archives`` from a tree listing."""
    prefix = f"datasets/{folder}/"
    by_name: dict[str, RemoteFile] = {}
    for entry in entries:
        if not entry.path.startswith(prefix):
            continue
        name = entry.path[len(prefix) :]
        if "/" in name or not name:
            continue
        by_name[name] = entry
    if "md5.txt" not in by_name:
        raise ValueError(f"datasets/{folder}/md5.txt is not in the tree")
    md5_entry = by_name["md5.txt"]
    selected: list[PlannedFile] = [
        PlannedFile(path=md5_entry.path, size=md5_entry.size, archive=None, role="md5")
    ]
    wanted = set(archives)
    for archive in archives:
        parts = _parts_for(archive, by_name)
        if not parts:
            raise ValueError(f"{archive} is not in datasets/{folder}")
        for name in parts:
            entry = by_name[name]
            role = "zip" if name.endswith(".zip") else "part"
            selected.append(
                PlannedFile(path=entry.path, size=entry.size, archive=archive, role=role)
            )
        wanted.discard(archive)
    if wanted:
        raise ValueError(f"missing archives: {', '.join(sorted(wanted))}")
    return tuple(selected)


def _parts_for(archive: str, by_name: dict[str, RemoteFile]) -> list[str]:
    parts: list[tuple[int, str]] = []
    zip_name = f"{archive}.zip"
    has_zip = zip_name in by_name
    for name in by_name:
        match = _PART.fullmatch(name)
        if match and match.group("base") == archive:
            parts.append((int(match.group("index")), name))
    parts.sort()
    ordered = [name for _, name in parts]
    if has_zip:
        ordered.append(zip_name)
    return ordered


def required_free_bytes(files: tuple[PlannedFile, ...], *, extract: bool) -> int:
    """Bytes that must be free before the first download.

    The download itself needs the sum of the selected sizes. Extraction adds
    another copy of each compressed archive (a 2× lower bound; ASCII PLY
    expands further and is rechecked from ``ZipInfo.file_size``). A split
    archive adds a third copy: the reassembled zip.
    """
    download = sum(item.size for item in files)
    if not extract:
        return download
    compressed = 0
    reassembly = 0
    by_archive: dict[str, list[PlannedFile]] = {}
    for item in files:
        if item.archive is None:
            continue
        by_archive.setdefault(item.archive, []).append(item)
    for group in by_archive.values():
        compressed += sum(item.size for item in group)
        if any(item.role == "part" for item in group):
            reassembly += sum(item.size for item in group)
    return download + compressed + reassembly


def download_griffin(
    dest: Path,
    *,
    subset: str,
    components: str | None = None,
    preset: str | None = None,
    dry_run: bool = False,
    accept_terms: bool = False,
    hub: Hub | None = None,
    token: str | bool | None = None,
    free_bytes: Callable[[Path], int] | None = None,
    ack_file: Path | None = None,
    extract: bool = True,
    when: str | None = None,
) -> DownloadReport:
    """Download one subset into ``dest / <folder>`` and, by default, extract it."""
    folder = folder_for(subset)
    archives = archives_for(preset=preset, components=components)
    client = hub if hub is not None else HfHub(_token_or_resolve(token))
    files = plan_files(client.list_files(folder), folder, archives)
    notice = assert_mit(client.read_dataset_card(), client.read_toolkit_license())
    need = required_free_bytes(files, extract=extract)
    ack = ack_file if ack_file is not None else ack_path()
    if dry_run:
        state = "already acknowledged" if acknowledgement_present(ack) else "not acknowledged"
        return DownloadReport(
            subset=subset,
            folder=folder,
            dest=dest / folder,
            files=files,
            required_free_bytes=need,
            notice=notice,
            acknowledgement=f"{state}; --dry-run does not write the acknowledgement",
            dry_run=True,
            self_md5_note="",
            downloaded=(),
            skipped=(),
            release_root=None,
        )
    if not acknowledgement_present(ack):
        if not accept_terms:
            raise LicenceError(
                f"{notice}\n"
                "Pass --accept-griffin-terms to record the acknowledgement "
                f"at {ack}. It is outside the repository and stores no token."
            )
        write_acknowledgement(notice, ack, when=when)
        ack_state = f"wrote {ack}"
    else:
        ack_state = f"already acknowledged at {ack}"
    root = dest / folder
    root.mkdir(parents=True, exist_ok=True)
    _require_space(root, need, free_bytes)
    downloaded, skipped = _fetch_all(client, root, files)
    note = _self_md5_note(root / "md5.txt")
    release: Path | None = None
    if extract:
        release = _extract_all(root, files, free_bytes)
    report = DownloadReport(
        subset=subset,
        folder=folder,
        dest=root,
        files=files,
        required_free_bytes=need,
        notice=notice,
        acknowledgement=ack_state,
        dry_run=False,
        self_md5_note=note,
        downloaded=tuple(downloaded),
        skipped=tuple(skipped),
        release_root=release,
    )
    (root / "DOWNLOAD.txt").write_text(_report_text(report), encoding="utf-8")
    return report


def _token_or_resolve(token: str | bool | None) -> str | bool:
    if token is None:
        return resolve_hf_token()
    return token


def _require_space(path: Path, need: int, free_bytes: Callable[[Path], int] | None) -> None:
    probe = path if path.exists() else path.parent
    free = shutil.disk_usage(probe).free if free_bytes is None else free_bytes(probe)
    if free < need:
        raise DiskSpaceError(f"need {need} bytes free under {probe}, have {free}")


def _fetch_all(
    client: Hub,
    root: Path,
    files: tuple[PlannedFile, ...],
) -> tuple[list[str], list[str]]:
    md5_item = next(item for item in files if item.role == "md5")
    payload_names = [PurePosixPath(item.path).name for item in files if item.role != "md5"]
    downloaded, skipped = _ensure_md5(client, root, md5_item, payload_names)
    listed = _parse_md5(root / "md5.txt")
    for item in files:
        if item.role == "md5":
            continue
        name = PurePosixPath(item.path).name
        expected = listed.get(name)
        if expected is None:
            raise ChecksumError(f"{name} is not listed in md5.txt")
        dest = root / "archives" / name
        status = _fetch_one(client, item, dest, expected=expected)
        (downloaded if status == "downloaded" else skipped).append(item.path)
    verify_md5(root / "archives", payload_names)
    return downloaded, skipped


def _ensure_md5(
    client: Hub,
    root: Path,
    item: PlannedFile,
    payload_names: list[str],
) -> tuple[list[str], list[str]]:
    """Keep a local ``md5.txt`` when it already lists every selected payload.

    The file's own listed digest does not match its bytes, so that line cannot
    be the resume key. A local file that names the payloads is enough; each
    payload is still checked against the digest on its own line.
    """
    dest = root / "md5.txt"
    if dest.is_file() and _md5_lists(dest, payload_names):
        return [], [item.path]
    if dest.exists():
        dest.unlink()
    client.download(item.path, dest)
    if not _md5_lists(dest, payload_names):
        missing = [name for name in payload_names if name not in _parse_md5(dest)]
        raise ChecksumError(f"md5.txt does not list {', '.join(missing)}")
    return [item.path], []


def _md5_lists(path: Path, names: list[str]) -> bool:
    try:
        listed = _parse_md5(path)
    except (OSError, ValueError):
        return False
    return all(name in listed for name in names)


def _fetch_one(client: Hub, item: PlannedFile, dest: Path, *, expected: str | None) -> str:
    if expected is not None and dest.is_file() and _md5_file(dest) == expected:
        return "skipped"
    if dest.exists():
        dest.unlink()
    client.download(item.path, dest)
    if expected is not None and _md5_file(dest) != expected:
        raise ChecksumError(f"{dest.name} md5 {_md5_file(dest)} != {expected}")
    return "downloaded"


def verify_md5(directory: Path, names: list[str]) -> None:
    """Check ``names`` against ``md5.txt``.

    The published file includes a hash of itself that does not match the file
    on disk (measured 2026-10-15). That line is ignored. Payload lines are not.

    ``md5.txt`` may sit in ``directory`` or in its parent (the subset root,
    with payloads under ``archives/``).
    """
    listed = _md5_table_for(directory)
    for name in names:
        if name == "md5.txt":
            continue
        if name not in listed:
            raise ChecksumError(f"{name} is not listed in md5.txt")
        expected = listed[name]
        digest = _md5_file(directory / name)
        if digest != expected:
            raise ChecksumError(f"{name} md5 {digest} != {expected}")


def _md5_table_for(directory: Path) -> dict[str, str]:
    local = directory / "md5.txt"
    parent = directory.parent / "md5.txt"
    path = local if local.is_file() else parent
    return _parse_md5(path)


def _parse_md5(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split()
        rows[name.removeprefix("./")] = digest.lower()
    return rows


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _self_md5_note(path: Path) -> str:
    listed = _parse_md5(path).get("md5.txt")
    computed = _md5_file(path)
    if listed is None:
        return "md5.txt has no self-line; payload lines were checked"
    if listed == computed:
        return f"md5.txt self-line matches {computed}"
    return (
        f"md5.txt self-line {listed} != computed {computed}; "
        "the self-line was ignored and payload lines were checked"
    )


def _extract_all(
    root: Path,
    files: tuple[PlannedFile, ...],
    free_bytes: Callable[[Path], int] | None,
) -> Path:
    release = root / "griffin-release"
    release.mkdir(parents=True, exist_ok=True)
    by_archive: dict[str, list[PlannedFile]] = {}
    for item in files:
        if item.archive is not None:
            by_archive.setdefault(item.archive, []).append(item)
    layout: list[dict[str, object]] = []
    for archive, group in by_archive.items():
        archive_path = _archive_path(root, archive, group)
        marker = root / ".extracted" / f"{archive}.md5"
        digest = _md5_file(archive_path)
        if (
            marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == digest
            and _has_side(release)
        ):
            continue
        with zipfile.ZipFile(archive_path) as opened:
            _write_member_sample(root, archive, opened)
            _require_space(release, _uncompressed_size(opened), free_bytes)
            names = _extract_zip(opened, release)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(digest + "\n", encoding="utf-8")
        layout.append({"archive": archive, "members": len(names), "sample": names[:30]})
    if not _has_side(release):
        raise RuntimeError(
            f"{release} has neither vehicle-side nor drone-side after extract; sample {layout[:1]}"
        )
    if layout:
        (root / "extract-layout.json").write_text(_layout_text(layout), encoding="utf-8")
    return release


def _has_side(release: Path) -> bool:
    return any((release / side).is_dir() for side in _SIDES)


def _write_member_sample(root: Path, archive: str, opened: zipfile.ZipFile) -> None:
    """Record a few raw member names before extract, so a bad layout is visible."""
    sample = [info.filename for info in opened.infolist()[:20]]
    path = root / "zip-members.txt"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{archive} members={len(opened.infolist())}\n")
        for name in sample:
            handle.write(f"  {name}\n")


def _uncompressed_size(opened: zipfile.ZipFile) -> int:
    return sum(info.file_size for info in opened.infolist())


def _archive_path(root: Path, archive: str, group: list[PlannedFile]) -> Path:
    names = [PurePosixPath(item.path).name for item in group]
    if not any(_PART.fullmatch(name) for name in names):
        return root / "archives" / f"{archive}.zip"
    ordered = _sort_parts(names)
    dest = root / "reassembled" / f"{archive}.zip"
    part_bytes = sum((root / "archives" / name).stat().st_size for name in ordered)
    if dest.is_file() and dest.stat().st_size == part_bytes:
        _open_zip(dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".partial")
    with temporary.open("wb") as output:
        for name in ordered:
            with (root / "archives" / name).open("rb") as source:
                shutil.copyfileobj(source, output)
    os.replace(temporary, dest)
    _open_zip(dest)
    return dest


def _sort_parts(names: list[str]) -> list[str]:
    parts: list[tuple[int, str]] = []
    zips: list[str] = []
    for name in names:
        match = _PART.fullmatch(name)
        if match:
            parts.append((int(match.group("index")), name))
        elif name.endswith(".zip"):
            zips.append(name)
    parts.sort()
    return [name for _, name in parts] + zips


def _open_zip(path: Path) -> None:
    """Fail if the reassembled bytes are not a readable zip central directory."""
    with zipfile.ZipFile(path) as opened:
        bad = opened.testzip()
    if bad is not None:
        raise ChecksumError(f"{path.name} member {bad} failed the zip CRC")


def _extract_zip(opened: zipfile.ZipFile, release: Path) -> list[str]:
    written: list[str] = []
    for info in opened.infolist():
        relative = _member_relative(info.filename)
        if relative is None:
            continue
        target = release / relative
        if not _inside(release, target):
            raise ValueError(f"zip member escapes the release: {info.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with opened.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        written.append(info.filename)
    return written


def _member_relative(name: str) -> str | None:
    if name.endswith("/"):
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("__MACOSX/") or "/__MACOSX/" in f"/{normalized}":
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError(f"absolute zip member {name}")
    parts = list(path.parts)
    if ".." in parts:
        raise ValueError(f"zip member escapes the archive: {name}")
    if "griffin-release" in parts:
        parts = parts[parts.index("griffin-release") + 1 :]
    while parts and parts[0] not in _SIDES and any(part in _SIDES for part in parts[1:]):
        parts = parts[1:]
    if not parts:
        return None
    return "/".join(parts)


def _inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _layout_text(layout: list[dict[str, object]]) -> str:
    import json

    return json.dumps(layout, indent=2) + "\n"


def _report_text(report: DownloadReport) -> str:
    lines = [
        report.notice,
        report.acknowledgement,
        report.self_md5_note,
        f"required_free_bytes={report.required_free_bytes}",
    ]
    for item in report.files:
        lines.append(f"{item.size}\t{item.path}")
    text = "\n".join(lines) + "\n"
    if "HF_TOKEN" in text:
        raise LicenceError("refusing to write a download note that contains a token")
    return text


def format_report(report: DownloadReport) -> str:
    lines = [report.notice, report.acknowledgement]
    if report.self_md5_note:
        lines.append(report.self_md5_note)
    lines.append(f"required_free_bytes={report.required_free_bytes}")
    for item in report.files:
        lines.append(f"{item.size}\t{item.path}")
    if report.release_root is not None:
        lines.append(f"release={report.release_root}")
    return "\n".join(lines)
