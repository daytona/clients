# Agent Development Guide

This repository contains Daytona client-facing packages: SDKs, generated API clients, the CLI with MCP support, examples, and vendored OpenAPI specs.

Use the Nix dev shells (`nix develop`) when running builds locally — this repo is **Nix-only** (no devcontainer). Keep changes scoped to the client repo; platform services are intentionally not present here.

## Dev Shells

| Shell | Command | Use |
|---|---|---|
| `default` | `nix develop` | All client toolchains |
| `go` | `nix develop .#go` | CLI, Go SDK, Go generated clients |
| `node` | `nix develop .#node` | TypeScript SDK and generated clients |
| `python` | `nix develop .#python` | Python SDK and generated clients |
| `ruby` | `nix develop .#ruby` | Ruby SDK and generated clients |
| `java` | `nix develop .#java` | Java SDK and generated clients |

Each shell **auto-installs** its language's dependencies on first entry (idempotent — instant on re-entry; set `DAYTONA_NO_BOOTSTRAP=1` to skip). So `nix develop` leaves you fully ready.

## Project Map

| Path | Purpose |
|---|---|
| `cli` | Daytona CLI and embedded MCP server (Go) |
| `sdk-typescript` | TypeScript SDK (`@daytona/sdk`) |
| `sdk-python` | Python SDK (`daytona`) |
| `sdk-ruby` | Ruby SDK (`daytona`) |
| `sdk-java` | Java SDK (`io.daytona:sdk-java`) |
| `sdk-go` | Go SDK (`github.com/daytona/clients/sdk-go`) |
| `api-client*` | Generated Daytona API clients |
| `toolbox-api-client*` | Generated toolbox API clients |
| `analytics-api-client` | Generated analytics API client |
| `openapi-specs` | Vendored specs for local client generation |
| `examples` | SDK examples (Go examples are a workspace module: `examples/go/go.mod`) |
| `hack` | OpenAPI generator templates and postprocess scripts |
| `artifacts/sdk-docs` | Generated SDK reference MDX output |

## Setup

Just enter the dev shell — it installs every language's dependencies on first entry:

```bash
nix develop          # all languages, fully bootstrapped
nix develop .#go     # or one language: .#node / .#python / .#ruby / .#java
```

The shellHook runs `yarn install` / `poetry install` / `bundle install` / `go work sync`
as needed; it's idempotent (instant on re-entry). Skip it with `DAYTONA_NO_BOOTSTRAP=1`.
Java has no install step — Gradle resolves the SDK via composite build on first run.

If `nix develop` fails with a Nix daemon permission error, the Nix daemon isn't running.
Start it (`sudo systemctl start nix-daemon` on systemd hosts) and retry.

## Common Commands

```bash
yarn generate:api-client
yarn build
yarn test
yarn docs
```

```bash
go work sync
go build ./cli/...
go test ./sdk-go/...
go run ./examples/go/exec_sessions     # examples/go is a workspace module
```

## Running examples against a live API

Examples talk to a real Daytona API. Set credentials, then run per language:

```bash
export DAYTONA_API_KEY=...   DAYTONA_API_URL=https://app.daytona.io/api

nix develop .#python --command poetry run python examples/python/exec-command/exec.py
nix develop .#node   --command node_modules/.bin/tsx examples/typescript/exec-command/index.ts
nix develop .#ruby   --command bundle exec ruby examples/ruby/exec-command/exec.rb
nix develop .#go     --command go run ./examples/go/exec_sessions
nix develop .#java   --command bash -c 'cd examples/java/exec-command && gradle run'
```

## Rules

- Keep the CLI (`cli`) and SDKs (`sdk-*`) / generated clients (`*-api-client*`) as the top-level package boundary.
- Keep generated clients pointed at `openapi-specs/*`.
- Generate SDK reference docs into `artifacts/sdk-docs/`, not a docs app.
- Do not add platform apps, dashboard/docs app code, private runner/daemon code, or integration plugins to this repo.
- Do not commit or push unless explicitly asked.
