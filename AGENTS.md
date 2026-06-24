# Agent Development Guide

This repository contains Daytona client-facing packages: SDKs, generated API clients, the CLI with MCP support, examples, and vendored OpenAPI specs.

Use the Nix dev shells or the devcontainer when running builds locally. Keep changes scoped to the client repo; platform services are intentionally not present here.

## Dev Shells

| Shell | Command | Use |
|---|---|---|
| `default` | `nix develop` | All client toolchains |
| `go` | `nix develop .#go` | CLI, Go SDK, Go generated clients |
| `node` | `nix develop .#node` | TypeScript SDK and generated clients |
| `python` | `nix develop .#python` | Python SDK and generated clients |
| `ruby` | `nix develop .#ruby` | Ruby SDK and generated clients |
| `java` | `nix develop .#java` | Java SDK and generated clients |

## Project Map

| Path | Purpose |
|---|---|
| `apps/cli` | Daytona CLI and embedded MCP server |
| `libs/sdk-typescript` | TypeScript SDK |
| `libs/sdk-python` | Python SDK |
| `libs/sdk-ruby` | Ruby SDK |
| `libs/sdk-java` | Java SDK |
| `libs/sdk-go` | Go SDK |
| `libs/api-client*` | Generated Daytona API clients |
| `libs/toolbox-api-client*` | Generated toolbox API clients |
| `libs/analytics-api-client*` | Generated analytics API clients |
| `openapi-specs` | Vendored specs for local client generation |
| `examples` | SDK examples |
| `hack` | OpenAPI generator templates and postprocess scripts |
| `artifacts/sdk-docs` | Generated SDK reference MDX output |

## Common Commands

```bash
yarn install
poetry install
bundle install
```

```bash
yarn generate:api-client
yarn build
yarn test
yarn docs
```

```bash
go work sync
go build ./apps/cli/...
go test ./libs/sdk-go/...
```

```bash
DAYTONA_API_KEY=... DAYTONA_API_URL=https://app.daytona.io/api yarn test:e2e
```

## Rules

- Keep `apps/cli` and `libs/*` as the app/lib boundary.
- Keep generated clients pointed at `openapi-specs/*`.
- Generate SDK reference docs into `artifacts/sdk-docs/`, not a docs app.
- Do not add platform apps, dashboard/docs app code, private runner/daemon code, or integration plugins to this repo.
- Do not commit or push unless explicitly asked.
