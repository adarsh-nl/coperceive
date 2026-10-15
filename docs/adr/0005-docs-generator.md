# ADR 0005 — Docs generator

Status: accepted for the skeleton.

## Context

Design notes and ADRs are Markdown in the repository. They must not be git-ignored. The site is a reader for those files, not a second source of truth.

## Options

1. MkDocs with the Material theme.
2. Sphinx with MyST, so it also reads Markdown.

## Measurement

One page, the sentence “A short page used only to time the generator.” Wall clock from `bash` `TIMEFORMAT`, 2026-10-15, this VM. Numbers are in `docs/design/measurements/doc_and_web.json`.

| Generator | Version | Wall clock |
| --- | --- | ---: |
| MkDocs Material | mkdocs 1.6.1, material 9.7.7 | 0.408 s (MkDocs reported 0.21 s) |
| Sphinx + MyST | Sphinx 9.1.0, MyST 5.1.0 | 0.505 s |

A first Sphinx attempt looked for `index.rst` and failed. That failure is not the timing above. The successful run set `source_suffix` to Markdown.

The gap on one page is small. It does not predict a large site.

## Criteria

- Markdown stays the file the ADRs are written in.
- A person can build the site with one extra.
- The result is readable without writing a theme.

## Decision

MkDocs Material. Config: `mkdocs.yml`. Extra: `pip install -e ".[docs]"` then `mkdocs serve` or `mkdocs build`.

## Consequences

- Sphinx and MyST are not dependencies.
- API docs from docstrings are not generated. The docstrings are still the library reference until a later phase adds them.
