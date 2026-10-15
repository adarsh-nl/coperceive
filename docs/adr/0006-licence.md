# ADR 0006 — Licence

Status: accepted. The licence is Apache-2.0. The repository stays private.

## Context

The project is meant to be open source and owned by Adarsh. Dependencies and datasets have their own terms. Griffin’s toolkit is MIT. Griffin’s recordings are not redistributed by this repository. OpenCOOD's academic-only licence is a poor fit for a library other labs should be able to ship.

## Options

1. Apache-2.0.
2. MIT.
3. A custom academic licence.

## Criteria

- OSI licence, so universities and companies can use it without a side letter.
- An explicit patent grant.
- Compatible with viser (Apache-2.0), the nuScenes devkit’s stated Apache terms, UniV2X (Apache-2.0), and one of Rerun’s two licences.

## Decision

Recommend Apache-2.0. The tree uses that text until Adarsh says otherwise. Copyright line: Adarsh (github.com/adarsh-nl), 2026. No surname and no email were invented.

## Consequences

- If the choice becomes MIT, replace `LICENSE`, the `pyproject.toml` classifier, and `CITATION.cff` together.
- Third-party notices for Griffin’s toolkit licence stay in `THIRD_PARTY_NOTICES.md` either way.
- The name is coperceive. Apache-2.0 is the licence. The repository stays private.
