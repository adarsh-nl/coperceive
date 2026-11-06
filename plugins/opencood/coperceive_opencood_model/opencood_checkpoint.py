# SPDX-License-Identifier: Apache-2.0
"""The checkpoint archive: hashing, safe extraction and finding its config and weights.

The archive is user-supplied and its contents are not known in advance (the cluster copy's
layout is unverified). This module therefore discovers rather than assumes: a config YAML and
a weights file are found by suffix, ambiguity is an error that lists what the archive holds
(so a cluster check can pick one with the ``checkpoint_config``/``checkpoint_weights``
options), and the MD5 of the archive itself is what ``hello.environment.checkpoint_md5``
reports, so the parent hashes the same file and the cache key follows its bytes.

Only ``.zip`` is supported in this version; the cluster fact we were given is a zip.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile

CONFIG_SUFFIXES = (".yaml", ".yml")
# The only names the plugin accepts from discovery: an OpenCOOD checkpoint directory's own
# "config.yaml" (train.py/setup_train writes that name). A differently named YAML is accepted
# only through the explicit --option checkpoint_config=... override.
CONFIG_NAMES = ("config.yaml", "config.yml")
WEIGHTS_SUFFIXES = (".pth", ".pt", ".ckpt")
# Names to prefer when an archive holds several candidates, best first. Case-insensitive.
CONFIG_PREFERRED = ("config", "hypes", "cobevt")
WEIGHTS_PREFERRED = ("bestval", "best", "latest", "epoch")


class CheckpointError(ValueError):
    """The archive is missing, is not a zip, or does not hold one config and one weights file."""


def md5_of(path: str) -> str:
    """MD5 of a file, read in chunks (the protocol's checkpoint digest; not a security check)."""
    digest = hashlib.md5()  # noqa: S324 - the protocol documents MD5 digests
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: str) -> list[str]:
    """Every file under ``root`` as a forward-slash virtual path, sorted."""
    out: list[str] = []
    for directory, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(directory, name)
            relative = os.path.relpath(full, root)
            out.append(relative.replace(os.sep, "/"))
    out.sort()
    return out


def _pick(
    names: list[str],
    suffixes: tuple[str, ...],
    preferred: tuple[str, ...],
    what: str,
    hint: str,
) -> str:
    """One name from ``names`` with a matching suffix; ambiguity names every candidate."""
    if hint:
        if hint not in names:
            raise CheckpointError(f"{what} {hint!r} is not in the archive")
        return hint
    candidates = [name for name in names if name.lower().endswith(suffixes)]
    if len(candidates) == 1:
        return candidates[0]
    for token in preferred:
        matching = [name for name in candidates if token in name.lower()]
        if len(matching) == 1:
            return matching[0]
    if not candidates:
        raise CheckpointError(
            f"no {what} ({', '.join(suffixes)}) in the archive; it holds: "
            + (", ".join(names) if names else "no files")
        )
    raise CheckpointError(
        f"{len(candidates)} possible {what} files in the archive; pass "
        f"--option checkpoint_{what}=<path inside the archive>. Candidates: "
        + ", ".join(candidates)
    )


def extract(archive: str, destination: str) -> list[str]:
    """Extract a zip archive under ``destination``, refusing members that escape it.

    Returns the virtual paths written. Only regular members are written; nothing is executed.
    """
    if not os.path.isfile(archive):
        raise CheckpointError(f"checkpoint {archive!r} is not a file")
    if not archive.lower().endswith(".zip"):
        raise CheckpointError(
            f"checkpoint {archive!r} is not a .zip; this version reads zip archives only. "
            "Check the file with `file` and `unzip -l`, then point --option checkpoint= at "
            "the zip (or re-zip an extracted checkpoint directory)."
        )
    root = os.path.abspath(destination)
    written: list[str] = []
    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                name = member.filename.replace("\\", "/")
                parts = [part for part in name.split("/") if part]
                if not parts or name.endswith("/"):
                    continue
                if any(part in ("..", ".") for part in parts) or name.startswith("/"):
                    raise CheckpointError(f"{archive}: refusing archive member {member.filename!r}")
                target = os.path.abspath(os.path.join(root, *parts))
                if os.path.commonpath([root, target]) != root:
                    raise CheckpointError(f"{archive}: refusing archive member {member.filename!r}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with bundle.open(member) as source, open(target, "wb") as handle:
                    shutil.copyfileobj(source, handle, 1 << 20)
                written.append("/".join(parts))
    except zipfile.BadZipFile as exc:
        raise CheckpointError(f"{archive}: not a readable zip archive ({exc})") from None
    if not written:
        raise CheckpointError(f"{archive}: the archive holds no files")
    return sorted(written)


def _regular_file(path: str, label: str) -> None:
    """Refuse a config/weights path that is not a plain, non-empty regular file."""
    if os.path.islink(path):
        raise CheckpointError(
            f"{label} {path!r} is a symbolic link; the checkpoint must hold the file itself"
        )
    if not os.path.isfile(path):
        raise CheckpointError(f"{label} {path!r} is not a regular file")
    if os.path.getsize(path) == 0:
        raise CheckpointError(f"{label} {path!r} is empty")


def discover(root: str, *, config_hint: str = "", weights_hint: str = "") -> tuple[str, str]:
    """``(config path, weights path)`` for an extracted checkpoint directory.

    Discovery refuses to guess the config: without ``config_hint`` the chosen YAML must be
    named ``config.yaml``/``config.yml`` (the name OpenCOOD's own training writes). Any other
    name is an error that lists the YAMLs found and asks for
    ``--option checkpoint_config=<path inside the archive>``. Symlinked or non-regular files,
    and an empty weights file, are refused; the archive extraction never writes links.
    """
    names = _walk(root)
    config_rel = _pick(names, CONFIG_SUFFIXES, CONFIG_PREFERRED, "config", config_hint)
    if not config_hint and os.path.basename(config_rel).lower() not in CONFIG_NAMES:
        yamls = [name for name in names if name.lower().endswith(CONFIG_SUFFIXES)]
        raise CheckpointError(
            f"the discovered config {config_rel!r} is not named config.yaml/config.yml; "
            f"YAMLs in the archive: {', '.join(yamls) or 'none'}; pass "
            "--option checkpoint_config=<path inside the archive> to choose it explicitly"
        )
    weights_rel = _pick(names, WEIGHTS_SUFFIXES, WEIGHTS_PREFERRED, "weights", weights_hint)
    config_path = os.path.join(root, *config_rel.split("/"))
    weights_path = os.path.join(root, *weights_rel.split("/"))
    _regular_file(config_path, "config")
    _regular_file(weights_path, "weights")
    return config_path, weights_path


def prepare(
    archive: str, destination: str, *, config_hint: str = "", weights_hint: str = ""
) -> tuple[str, str, list[str]]:
    """Extract ``archive`` under ``destination`` and return config, weights and written names."""
    written = extract(archive, destination)
    config_path, weights_path = discover(
        destination, config_hint=config_hint, weights_hint=weights_hint
    )
    return config_path, weights_path, written
