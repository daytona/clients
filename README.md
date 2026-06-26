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
go get go.daytona.io/sdk-go
```

### Java

```kotlin
dependencies {
  implementation("io.daytona:sdk:x.y.z")
}
```

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
