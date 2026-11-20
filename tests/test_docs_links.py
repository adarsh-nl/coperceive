# SPDX-License-Identifier: Apache-2.0
"""Test that documentation links and community files resolve and contain no leaks."""

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import cast


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_community_standards_files_exist() -> None:
    """Verify that all GitHub community standard files exist."""
    root = _repo_root()
    expected_files = [
        root / "README.md",
        root / "CHANGELOG.md",
        root / "LICENSE",
        root / "CONTRIBUTING.md",
        root / "CODE_OF_CONDUCT.md",
        root / "SECURITY.md",
        root / "SUPPORT.md",
        root / "ACCESSIBILITY.md",
        root / ".github" / "pull_request_template.md",
        root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        root / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml",
        root / ".github" / "ISSUE_TEMPLATE" / "dataset_request.yml",
        root / ".github" / "ISSUE_TEMPLATE" / "config.yml",
    ]
    for path in expected_files:
        assert path.is_file(), f"Expected community file missing: {path}"


def test_documentation_relative_links_resolve() -> None:
    """Extract all relative markdown links and verify target files exist."""
    root = _repo_root()
    doc_files = [
        root / "README.md",
        root / "CHANGELOG.md",
        root / "CONTRIBUTING.md",
        root / "CODE_OF_CONDUCT.md",
        root / "SECURITY.md",
        root / "SUPPORT.md",
        root / "ACCESSIBILITY.md",
        root / ".github" / "pull_request_template.md",
    ]

    link_pattern = re.compile(r"!?\[(?:[^\]]*)\]\(([^)]+)\)")

    checked_links = 0
    for doc in doc_files:
        assert doc.is_file(), f"Doc file missing: {doc}"
        content = doc.read_text(encoding="utf-8")
        for match in link_pattern.finditer(content):
            target = match.group(1).strip()
            # Skip pure URL anchors or mailto / external URLs
            if target.startswith(("#", "http://", "https://", "mailto:")):
                continue

            # Strip any in-file anchor, e.g. path/to/file.md#anchor
            clean_target = target.split("#")[0]
            if not clean_target:
                continue

            # Resolve relative to the document or repo root
            resolved_from_doc = (doc.parent / clean_target).resolve()
            resolved_from_root = (root / clean_target).resolve()

            exists = resolved_from_doc.exists() or resolved_from_root.exists()
            assert exists, (
                f"Broken link in {doc.relative_to(root)}: target '{target}' does not exist"
            )
            checked_links += 1

    assert checked_links >= 5, f"Expected at least 5 relative links checked, found {checked_links}"


def test_no_personal_identifiers_in_docs() -> None:
    """Ensure no personal identifiers leak into documentation or templates."""
    root = _repo_root()
    doc_paths = [
        root / "README.md",
        root / "CHANGELOG.md",
        root / "CONTRIBUTING.md",
        root / "CODE_OF_CONDUCT.md",
        root / "SECURITY.md",
        root / "SUPPORT.md",
        root / "ACCESSIBILITY.md",
        root / ".github" / "pull_request_template.md",
    ] + list((root / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))

    forbidden_patterns = ["nanjaiyalathaa", "ewi.utwente.nl"]

    for path in doc_paths:
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8").lower()
        for forbidden in forbidden_patterns:
            assert forbidden not in content, (
                f"Forbidden personal identifier '{forbidden}' found in {path}"
            )


def _nav_pages(node: object, found: set[str]) -> None:
    if isinstance(node, str) and node.endswith(".md"):
        found.add(node)
        return
    if isinstance(node, dict):
        mapping = cast(dict[object, object], node)
        for value in mapping.values():
            _nav_pages(value, found)
        return
    if isinstance(node, list):
        items = cast(list[object], node)
        for item in items:
            _nav_pages(item, found)


def _exclude_match(pattern: str, relative: str) -> bool:
    """Match one MkDocs exclude_docs gitignore-style pattern."""
    import re

    if pattern.endswith("/"):
        prefix = pattern if pattern.endswith("/") else pattern + "/"
        return relative.startswith(prefix) or (relative + "/") == prefix
    pieces: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            pieces.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            pieces.append(".*")
            index += 2
        elif pattern[index] == "*":
            pieces.append("[^/]*")
            index += 1
        else:
            pieces.append(re.escape(pattern[index]))
            index += 1
    return re.fullmatch("".join(pieces), relative) is not None


def test_every_docs_page_is_in_the_nav_or_excluded() -> None:
    """A Markdown file under docs/ is on the public nav, or excluded on purpose."""
    import yaml

    root = _repo_root()
    config = yaml.safe_load((root / "mkdocs.yml").read_text(encoding="utf-8"))
    listed: set[str] = set()
    _nav_pages(config["nav"], listed)
    patterns = [
        line.strip()
        for line in str(config["exclude_docs"]).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    docs = root / "docs"
    missing: list[str] = []
    for path in sorted(docs.rglob("*.md")):
        relative = path.relative_to(docs).as_posix()
        if relative in listed or any(_exclude_match(pattern, relative) for pattern in patterns):
            continue
        missing.append(relative)
    assert not missing, "Markdown pages neither in the nav nor excluded:\n" + "\n".join(missing)
    absent = sorted(page for page in listed if not (docs / page).is_file())
    assert not absent, "Nav entries that are not files:\n" + "\n".join(absent)


def test_cli_help_stabilizer_ignores_python_313_wrapping() -> None:
    """3.13 breaks usage lines elsewhere and repeats a short option's metavar."""
    import importlib.util

    root = _repo_root()
    script = root / "scripts" / "docs" / "render_cli.py"
    spec = importlib.util.spec_from_file_location("render_cli_stabilize", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    older = """usage: coperceive maps install [-h]
                               --town {Town01,Town02} --maps MAPS
  -o, --out OUT         Output .cpbundle path.
usage: coperceive dataset [-h] {catalog,detect} ...
"""
    newer = """usage: coperceive maps install [-h] --town
                               {Town01,Town02} --maps
                               MAPS
  -o OUT, --out OUT     Output .cpbundle path.
usage: coperceive dataset [-h] {catalog,detect}
                          ...
"""
    assert module._stabilize(older) == module._stabilize(newer)


def test_cli_reference_matches_argparse_help() -> None:
    """The CLI page is the generator's output, not a hand-written command list."""
    import importlib.util

    root = _repo_root()
    script = root / "scripts" / "docs" / "render_cli.py"
    spec = importlib.util.spec_from_file_location("render_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = module.render()
    actual = (root / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    assert actual == expected
    assert "coperceive adapters ingest" in actual
    assert "coperceive eval run" in actual


def test_code_of_conduct_enforcement_placeholder() -> None:
    """Ensure CODE_OF_CONDUCT.md retains the <CONTACT> placeholder without private emails."""
    root = _repo_root()
    coc = root / "CODE_OF_CONDUCT.md"
    content = coc.read_text(encoding="utf-8")
    assert "<CONTACT>" in content, "CODE_OF_CONDUCT.md must retain the '<CONTACT>' placeholder"
    # Ensure no email addresses were placed in CODE_OF_CONDUCT.md
    assert not re.search(r"[\w\.-]+@[\w\.-]+\.\w+", content), (
        "CODE_OF_CONDUCT.md should not contain real email addresses"
    )


# Explicit allowlist for tracked PNG/JPG assets under docs/ or site/ inside a
# screenshots/ directory. Intentional README / site media belongs in
# docs/media/ or site/public/ rather than generated screenshots/ folders.
ALLOWED_SCREENSHOT_PATHS: frozenset[str] = frozenset()
ALLOWED_SCREENSHOT_PREFIXES: tuple[str, ...] = (
    "docs/media/",
    "site/public/",
)
_SCREENSHOT_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg"})


def _is_allowlisted_screenshot(rel_posix: str) -> bool:
    return rel_posix in ALLOWED_SCREENSHOT_PATHS or rel_posix.startswith(
        ALLOWED_SCREENSHOT_PREFIXES
    )


def _find_disallowed_screenshot_images(rel_paths: Iterable[str]) -> list[str]:
    """Return sorted docs/ or site/ PNG/JPG paths under a screenshots/ dir not on the allowlist."""
    offenders: list[str] = []
    for raw in rel_paths:
        rel = raw.replace("\\", "/")
        path = PurePosixPath(rel)
        if not path.parts or path.parts[0] not in {"docs", "site"}:
            continue
        if path.suffix.lower() not in _SCREENSHOT_IMAGE_SUFFIXES:
            continue
        if "screenshots" not in path.parts[1:-1]:
            continue
        if _is_allowlisted_screenshot(rel):
            continue
        offenders.append(rel)
    return sorted(offenders)


def _tracked_docs_and_site_paths(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "docs", "site"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        return [
            entry.decode("utf-8", errors="surrogateescape")
            for entry in proc.stdout.split(b"\0")
            if entry
        ]
    paths: list[str] = []
    for base_name in ("docs", "site"):
        base = root / base_name
        if not base.is_dir():
            continue
        for file_path in base.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(root).as_posix()
            if rel.startswith(("site/node_modules/", "site/dist/")):
                continue
            paths.append(rel)
    return paths


def test_no_committed_screenshots_in_docs_or_site() -> None:
    """Fail if any tracked PNG/JPG under docs/ or site/ is inside a screenshots/ directory."""
    root = _repo_root()
    tracked = _tracked_docs_and_site_paths(root)
    assert tracked, "Expected tracked files under docs/ or site/"
    offenders = _find_disallowed_screenshot_images(tracked)
    assert offenders == [], (
        "Generated screenshots must not be committed under docs/ or site/ "
        "(redirect test output to tests/.artifacts/ or add intentional media to "
        f"docs/media/ or site/public/): {offenders}"
    )


def test_screenshot_image_guard_catches_disallowed_paths() -> None:
    """Verify the image-path guard catches screenshots/ PNG/JPGs and respects allowlists."""
    sample = [
        "docs/design/screenshots/claude_design_mock_1600x1000.png",
        "docs/design/v0.1.1/screenshots/app-pages/local-1280x800-dark.jpg",
        "site/screenshots/preview.JPEG",
        "docs/media/Town03-000.png",
        "docs/media/screenshots/allowlisted.png",
        "site/public/og.png",
        "site/public/screenshots/allowlisted.jpg",
        "docs/design/screenshots/README.md",
        "tests/fixtures/screenshots/fixture.png",
    ]
    assert _find_disallowed_screenshot_images(sample) == [
        "docs/design/screenshots/claude_design_mock_1600x1000.png",
        "docs/design/v0.1.1/screenshots/app-pages/local-1280x800-dark.jpg",
        "site/screenshots/preview.JPEG",
    ]
