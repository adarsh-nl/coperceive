# ADR 0004 — Web app

Status: accepted for the Phase 0 shell. The stack was specified up front. The spike checked that it runs and what a thinner stack costs.

## Context

The page replaces a terminal for a person who already has the data on disk. It is one user, on one machine, sometimes behind JupyterHub.

## Options

1. FastAPI, Pydantic, Uvicorn, SQLite, React, TypeScript, Vite, an OpenAPI client, server-sent events plus polling.
2. Starlette alone, which FastAPI already uses, without the OpenAPI models.
3. A framework with sessions and CSRF cookies as a product (Django). Not implemented.

## Criteria

- OpenAPI for the browser client.
- Jobs start only from an argv allow-list, `shell=False`.
- Bind `127.0.0.1` unless a token is set.
- Paths stay under the server root.
- A mutating request carries a header so a foreign page cannot submit it as a simple form.
- `root_path` for a hub prefix.
- Static assets ship inside the wheel.

## Measurement

`docs/design/measurements/doc_and_web.json`. On this VM, `create_app` took 15.36 ms. One hundred `TestClient` health reads: median 1.403 ms, p95 2.012 ms. A one-route Starlette app: create 0.04 ms, health median 1.032 ms, p95 1.395 ms. FastAPI is the slower of the two by about 0.4 ms per health read here, and it is the one that emits the schema `web/openapi.json`.

## Decision

Option 1.

- `coperceive serve` defaults to `127.0.0.1:8731`. A host outside `{127.0.0.1, localhost, ::1}` without `--token` exits 2.
- When a token is set, `/api/*` requires `Authorization: Bearer <token>`.
- `POST`, `PUT`, `PATCH`, and `DELETE` require `X-Coperceive-Request: 1`.
- `confine` resolves the path and requires `is_relative_to` the root.
- The only allow-listed job name is `echo`. Anything else, including `bash`, is a 400. Arguments are a list. `shell` is false.
- Jobs are rows in SQLite under the server root.
- `GET /api/events` is a short server-sent stream. The page also polls `/api/jobs` every two seconds, because `EventSource` cannot set an Authorization header. The page uses `fetch` so it can.
- The UI is Vite, React, and TypeScript. `@radix-ui/react-label` labels the fields. TanStack Query holds health and jobs. `openapi-fetch` is generated from `web/openapi.json` into `web/src/api/schema.ts`. Vite `base` is `./`, and the build writes `coperceive/app/static`.
- `--root-path` is passed to FastAPI and Uvicorn. The page calls APIs relative to `document.baseURI`, so a prefix that the browser can see is kept.

## Consequences

- Phase 0 jobs do not convert datasets. Adding a command means editing `ALLOW` on purpose.
- The bearer token is a shared secret in the environment or the command line, not a user account. The UI keeps it in `sessionStorage`.
- Tornado’s default websocket cap (ADR 0003) does not apply to this HTTP API. It still applies if a later viewer opens a socket through a hub proxy.
- CSRF here is a custom header, not a cookie session. There is no login cookie to steal.
