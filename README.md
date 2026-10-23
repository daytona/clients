# Daytona Clients

SDKs, generated API clients, and the CLI/MCP tools for [Daytona](https://www.daytona.io/).

## Documentation

See the [Daytona documentation](https://www.daytona.io/docs) for guides, examples, and API reference material.

## Installation

### TypeScript

```bash
npm install @daytona/sdk
```

### Python

```bash
pip install daytona
```

### Ruby

```bash
gem install daytona
```

### Go

```bash
go get github.com/daytona/clients/sdk-go
```

### Java

```kotlin
dependencies {
  implementation("io.daytona:sdk:x.y.z")
}
```

### CLI

```bash
brew install daytonaio/cli/daytona
```

Or download the binary for your platform from [GitHub Releases](https://github.com/daytona/clients/releases). The release page lists it as `daytona-cli-<version>-<os>-<arch>` (`.exe` on Windows), and it downloads as `daytona-<os>-<arch>`.

#### Verify the binary

Release binaries carry a [GitHub artifact attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) (SLSA build provenance). Verify a downloaded binary with the [GitHub CLI](https://cli.github.com/):

```bash
gh attestation verify ./daytona-linux-amd64 --repo daytona/clients \
  --signer-workflow daytona/clients/.github/workflows/release.yaml
```

Verification matches the file's SHA-256 digest, so it works whatever the local file is named.

Verification fails with a 404 for a release that has no attestation, such as one published before attestations were introduced.

## Usage

```ts
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona({ apiKey: process.env.DAYTONA_API_KEY })
const sandbox = await daytona.create()
const response = await sandbox.process.codeRun('print("Hello Daytona")')

console.log(response.result)
```

Set `DAYTONA_API_KEY` and optionally `DAYTONA_API_URL` before running examples or E2E tests.

## Development

```bash
yarn install
poetry install
bundle install
```

Common commands:

```bash
yarn generate:api-client
yarn build
yarn test
yarn docs
```

OpenAPI specs used for local client generation live in `openapi-specs/`. SDK reference docs are generated as MDX into `artifacts/sdk-docs/`.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for contribution guidelines. Please keep changes scoped to SDKs, generated clients, CLI/MCP, examples, and client tooling.

## License

This repository uses a dual-license model. See [LICENSE](./LICENSE) for details.

- **CLI** (`cli/`): [AGPL-3.0](./cli/LICENSE)
- **Everything else** (SDKs, API clients, examples): [Apache-2.0](./LICENSE)
