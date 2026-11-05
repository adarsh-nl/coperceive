# SPDX-License-Identifier: Apache-2.0
"""Test that documentation links and community files resolve and contain no leaks."""

import re
from pathlib import Path


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
