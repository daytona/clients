# Contributing to Daytona Clients

This repository holds Daytona's client-facing packages — the **SDKs** (TypeScript, Python,
Ruby, Java, Go), the generated **API clients**, and the **CLI** (with its embedded MCP
server). Contributions are welcome! ❤️

> If you like the project but don't have time to contribute, you can still help by starring
> the repo, telling others about it, or referencing it in your own project's README.

## Code of Conduct

This project is governed by the [Daytona Code of Conduct](./CODE_OF_CONDUCT.md). By
participating, you are expected to uphold it. Please report unacceptable behavior to
[info@daytona.io](mailto:info@daytona.io).

## Provide feedback

Found a bug or have an idea for the SDKs, clients, or CLI? Please
[open an issue](https://github.com/daytona/clients/issues/new) — but first check that a
matching issue doesn't already exist. For questions and discussion, join the
[Daytona Community Slack](https://github.com/daytona/clients/slack).

## What you can contribute

- Bug fixes and new features in the SDKs (`sdk-typescript`, `sdk-python`, `sdk-ruby`,
  `sdk-java`, `sdk-go`).
- Improvements to the CLI and the embedded MCP server (`cli/`).
- New SDK examples under `examples/`.
- Improvements to the OpenAPI generation tooling under `hack/`.

> **Generated code**: `api-client*`, `toolbox-api-client*`, and `analytics-api-client*` are
> generated from the specs in `openapi-specs/` and the templates in `hack/`. Don't hand-edit
> the generated output — change the spec or the template and re-run `yarn generate:api-client`.

## Development setup

Use the Nix dev shells (`nix develop`, or `.#go` / `.#node` / `.#python` / `.#ruby` /
`.#java`) or the included devcontainer, then install dependencies:

```bash
yarn install
poetry install
bundle install
```

Common commands:

```bash
yarn generate:api-client   # regenerate clients from openapi-specs/
yarn build                 # build all packages
yarn test                  # run the test suites
yarn docs                  # generate SDK reference docs into artifacts/sdk-docs/
```

Go modules use a workspace:

```bash
go build ./cli/... ./sdk-go/...
go test ./sdk-go/...
```

See [`AGENTS.md`](./AGENTS.md) for the full project map and per-shell command reference.

## Coding style

Match the conventions already used in each package. Keep changes lint- and type-clean:

- **Go**: `gofmt` + `golangci-lint run`
- **TypeScript**: ESLint + Prettier (`yarn lint:ts`)
- **Python**: ruff / black / isort + basedpyright (`yarn lint:py`)
- **Ruby**: RuboCop
- **Java**: the module's Gradle build

Run `yarn lint` before opening a PR.

## Submitting a pull request

1. [Fork](https://help.github.com/articles/working-with-forks/) the repository and create a
   branch for your change.
2. Open (or find) a [GitHub issue](https://github.com/daytona/clients/issues) describing the
   change.
3. [Prepare your changes](./PREPARING_YOUR_CHANGES.md) with descriptive commits, and **sign off**
   every commit to comply with the DCO v1.1 (`git commit -s`).
4. If you changed generated clients, regenerate them (`yarn generate:api-client`) and commit
   the result. If you changed command behavior, regenerate any affected docs.
5. Ensure `yarn lint`, `yarn build`, and `yarn test` pass.
6. Open a pull request (a [draft PR](https://help.github.com/en/articles/about-pull-requests#draft-pull-requests)
   is welcome for early feedback). A Daytona team member will review it, and once approved and
   green it will be merged into `main`.

## Licensing

This repository is licensed under [Apache-2.0](./LICENSE). By contributing, you agree that
your contributions are licensed under Apache-2.0 and that you have the right to license them
(via the DCO sign-off on each commit).
