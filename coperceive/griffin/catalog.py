# SPDX-License-Identifier: Apache-2.0
"""Griffin subset names and the Hugging Face dataset they come from.

Archive basenames are the file names on the Hub without ``.zip`` or ``.zNN``.
``metadata`` and ``lidar`` are the only aliases. Any other component token is
an archive basename discovered from the tree, so a camera name does not have
to be hardcoded to be downloadable.
"""

from __future__ import annotations

REPO_ID = "wjh-svm/Griffin"
REPO_TYPE = "dataset"
TOOLKIT_REPO = "wang-jh18-SVM/Griffin"
TOOLKIT_LICENSE_URL = "https://raw.githubusercontent.com/wang-jh18-SVM/Griffin/main/LICENSE.txt"

SUBSETS: dict[str, str] = {
    "25m": "griffin_50scenes_25m",
    "40m": "griffin_50scenes_40m",
    "55m": "griffin_50scenes_55m",
    "random": "griffin_100scenes_random",
}

ALIASES: dict[str, tuple[str, ...]] = {
    "metadata": ("drone_metadata", "vehicle_metadata"),
    "lidar": ("vehicle_lidar",),
}

DEV_ARCHIVES: tuple[str, ...] = ("drone_metadata", "vehicle_metadata", "vehicle_lidar")

# A split part plus the closing zip. Reassembly concatenates z01, z02, ... then zip.
PART_WIDTH = 2


def folder_for(subset: str) -> str:
    try:
        return SUBSETS[subset]
    except KeyError:
        known = ", ".join(SUBSETS)
        raise ValueError(f"unknown subset {subset!r}; expected one of {known}") from None


def archives_for(*, preset: str | None, components: str | None) -> tuple[str, ...]:
    """Return archive basenames in the order they were requested."""
    if preset is not None and components is not None:
        raise ValueError("pass --preset or --components, not both")
    if preset is None and components is None:
        raise ValueError("pass --preset dev or --components")
    if preset is not None:
        if preset != "dev":
            raise ValueError(f"unknown preset {preset!r}; expected dev")
        return DEV_ARCHIVES
    assert components is not None
    names: list[str] = []
    for token in components.split(","):
        name = token.strip()
        if not name:
            continue
        if name in ALIASES:
            names.extend(ALIASES[name])
        elif name in {"md5.txt"} or "/" in name or name.startswith("."):
            raise ValueError(f"unknown component {name!r}")
        else:
            names.append(name)
    if not names:
        raise ValueError("no components selected")
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered)
