# Contributing to the Daytona SDKs and CLI

This repository holds the Daytona **Python SDK** (`daytona`), the **TypeScript
SDK** (`@daytona/sdk`) and the **`daytona` CLI**. Contributions are welcome! ❤️

> If you like the project but don't have time to contribute, you can still help by starring
> the repo, telling others about it, or referencing it in your own project's README.

## Code of Conduct

This project is governed by the [Daytona Code of Conduct](./CODE_OF_CONDUCT.md). By
participating, you are expected to uphold it. Please report unacceptable behavior to
[info@daytona.io](mailto:info@daytona.io).

## Provide feedback

Found a bug or have an idea for the SDKs or the CLI? Please
[open an issue](https://github.com/daytona/clients/issues/new) — but first check that a
matching issue doesn't already exist. For questions and discussion, join the
[Daytona Community Slack](https://go.daytona.io/slack). Security vulnerabilities go
through [SECURITY.md](./SECURITY.md), never a public issue.

## What you can contribute

- Bug fixes and new features in the SDKs (`sdk-python/`, `sdk-typescript/`). A change to SDK
  behavior lands in **both** SDKs in the same pull request — see the parity contract in
  [`AGENTS.md`](./AGENTS.md).
- Improvements to the CLI (`cli/`).
- New examples under `examples/`.

> **Generated code**: the wire types under `sdk-typescript/src/v2/gen/`,
> `sdk-python/src/daytona/v2/_gen/` and `cli/gen/` are generated from the API contracts in
> `contract/`, and the synchronous Python surface (`sdk-python/src/daytona/v2/_sync/`) is
> generated from the async one. Don't hand-edit generated output. The contract files
> themselves are maintained by the Daytona team; if you need API surface that isn't in
> them, open an issue.

## Development setup

You need Python 3.10+ with [uv](https://docs.astral.sh/uv/), Node.js 22.4+ and Go (the
version in `cli/go.mod`). Regenerating the wire types also needs a Java runtime.

```bash
(cd sdk-python && uv venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest -x -q)
(cd sdk-typescript && npm install && npm run typecheck && npm test && npm run lint && npm run build)
(cd cli && go vet ./... && go test ./...)
```

Before your first commit, enable the secret-scanning hooks (once per clone or
worktree):

```bash
scripts/secret-scan.sh install
```

It downloads a pinned, checksum-verified [gitleaks](https://github.com/gitleaks/gitleaks)
into the clone's `.git/` directory (Linux and macOS; elsewhere, put gitleaks on
your `PATH`) and enables the hooks in `.githooks/` for that worktree (git 2.36
or later): every commit's staged
changes and every push's new commits are checked against `.gitleaks.toml`, and
the `Secret scan` check repeats it on pull requests. If it blocks a commit,
remove the credential rather than bypassing the hook; for a value that is not a
secret, see the comments at the top of `.gitleaks.toml`.

With a Daytona API key you can also run the live end-to-end suites:
`DAYTONA_API_KEY=… ./e2e/run.sh`.

[`AGENTS.md`](./AGENTS.md) has the full project map and conventions.

## Coding style

Match the conventions already used in each package. Keep changes lint- and type-clean:

- **Python**: `ruff check` + `mypy` (strict)
- **TypeScript**: `tsc --strict` + `oxlint` (`npm run lint`)
- **Go**: `gofmt` + `golangci-lint run`

Every source file starts with a license header (see [Licensing](#licensing)); the
`License headers` check enforces it.

## Submitting a pull request

1. [Fork](https://help.github.com/articles/working-with-forks/) the repository and create a
   branch for your change.
2. Open (or find) a [GitHub issue](https://github.com/daytona/clients/issues) describing the
   change.
3. [Prepare your changes](./docs/PREPARING_YOUR_CHANGES.md) with descriptive commits, and **sign off**
   every commit to comply with the DCO v1.1 (`git commit -s`).
   The first time you open a PR, our CLA assistant will also ask you to **sign the
   [Contributor License Agreement](./CLA.md)** (see [Licensing](#licensing) below).
4. Add a changelog fragment under [`changelog.d/`](./changelog.d/README.md) for any
   user-visible change.
5. Ensure the commands under [Development setup](#development-setup) pass.
6. Open a pull request (a [draft PR](https://help.github.com/en/articles/about-pull-requests#draft-pull-requests)
   is welcome for early feedback). A Daytona team member will review it, and once approved and
   green it will be merged. Target 2.x pull requests at `main`; 0.x fixes go to
   `release/v0`. On PRs to `main`, the `CI passed` check sums up the CI workflows that
   ran on your PR (E2E aside); if it fails, open the failed workflow it names.

## Licensing

This repository is currently made available under [Apache-2.0](./LICENSE) (with `cli/` under
AGPL-3.0). Contributing involves two steps:

1. **DCO sign-off (per commit).** Sign off every commit with `git commit -s` to certify, under
   the [Developer Certificate of Origin](https://developercertificate.org/) v1.1, that you have
   the right to submit the code. See [PREPARING_YOUR_CHANGES.md](./docs/PREPARING_YOUR_CHANGES.md).

2. **CLA signature (once).** You must also sign the
   [Daytona Contributor License Agreement](./CLA.md) — it covers both individual and
   entity/corporate contributors. You (or, for entity contributors, your organization) retain
   copyright in your contributions,
   but you grant Daytona Platforms, Inc. a perpetual, irrevocable, sublicensable license to them —
   **including the right to relicense and redistribute the software under any terms (open source,
   proprietary, or closed source) and to change the license or visibility of the project at any
   time.** A CLA assistant bot will comment on your first pull request with a link and a one-line
   instruction to sign; a single signature covers all your future contributions to this repo.

Your PR cannot be merged until both the DCO check and the CLA check are green.
