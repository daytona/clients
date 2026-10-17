# Daytona TypeScript SDK

The official TypeScript SDK for [Daytona](https://daytona.io), a secure and elastic infrastructure for running AI-generated code. Daytona provides full composable computers — [sandboxes](https://www.daytona.io/docs/en/sandboxes/) — that you can manage programmatically using the Daytona SDK.

The SDK provides an interface for sandbox management, file system operations, Git operations, language server protocol support, process and code execution, and computer use. For more information, see the [documentation](https://www.daytona.io/docs/en/typescript-sdk/).

## Installation

Install the package using **npm**:

```bash
npm install @daytona/sdk
```

or using **yarn**:

```bash
yarn add @daytona/sdk
```

## Get API key

Generate an API key from the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/keys) to authenticate SDK requests and access Daytona services. For more information, see the [API keys](https://www.daytona.io/docs/en/api-keys/) documentation.

## Configuration

Configure the SDK using [environment variables](https://www.daytona.io/docs/en/configuration/#environment-variables) or by passing a [configuration object](https://www.daytona.io/docs/en/configuration/#configuration-in-code):

- `DAYTONA_API_KEY`: Your Daytona [API key](https://www.daytona.io/docs/en/api-keys/)
- `DAYTONA_API_URL`: The Daytona [API URL](https://www.daytona.io/docs/en/tools/api/)

```typescript
import { Daytona } from '@daytona/sdk'

// Initialize with environment variables
const daytona = new Daytona();

// Initialize with configuration object
const daytona = new Daytona({
  apiKey: 'YOUR_API_KEY',
});
```

To keep code portable to SDK 2.x, pass only `apiKey`, `jwtToken`, `organizationId`, the deprecated
`target` and `otelEnabled` to the root `Daytona`. The 2.x root `DaytonaConfig` has no `apiUrl`,
`serverUrl`, `useDeprecatedPolling`, `requestTimeoutMs` or `_experimental` (passing one is a type
error). The 2.x root client's `.v1` always uses
`V1_API_URL` and does not read `DAYTONA_API_URL`. If you need a custom endpoint, create the v1 client directly with `Daytona` from
`@daytona/sdk/v1`.

## Versioned namespace

Use the `v1` namespace for 2.x-compatible code today. Root resource methods are deprecated in 1.x and removed in 2.x.

```typescript
import { Daytona } from '@daytona/sdk'
import { Sandbox } from '@daytona/sdk/v1'

const daytona = new Daytona({ apiKey: 'YOUR_API_KEY' })
const sandbox: Sandbox = await daytona.v1.create({ language: 'typescript' })
```

## Create a sandbox

Create a sandbox to run your code securely in an isolated environment.

```typescript
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona({apiKey: "YOUR_API_KEY"});
const sandbox = await daytona.v1.create({
  language: 'typescript'
});
const response = await sandbox.process.codeRun('console.log("Hello World!")');
console.log(response.result);
```

## Examples and guides

Daytona provides [examples](https://www.daytona.io/docs/en/getting-started/#examples) and [guides](https://www.daytona.io/docs/en/guides/) for common sandbox operations, best practices, and a wide range of topics, from basic usage to advanced topics, showcasing various types of integrations between Daytona and other tools.

### Create a sandbox or snapshot in a specific region

Pass `target` in the sandbox create params, or `regionId` in the snapshot create params, to choose the [region](https://www.daytona.io/docs/en/regions/) for that call. The region is resolved per call: the per-call value, then the deprecated client-level `target` (or `DAYTONA_TARGET`, which emits a one-time deprecation warning), then the organization's default region.

```typescript
import { Daytona } from '@daytona/sdk'

const daytona = new Daytona()
const sandbox = await daytona.v1.create({ target: 'eu' })
const snapshot = await daytona.v1.snapshot.create({ name: 'my-snapshot', image: 'python:3.12', regionId: 'eu' })
```

### Create a sandbox with custom resources

Create a sandbox with [custom resources](https://www.daytona.io/docs/en/sandboxes/#resources) (CPU, memory, disk).

```typescript
import { Daytona } from '@daytona/sdk';
import { Image } from '@daytona/sdk/v1';

const daytona = new Daytona();
const sandbox = await daytona.v1.create({
    image: Image.debianSlim('3.12'),
    resources: { cpu: 2, memory: 4, disk: 8 }
});
```

### Create an ephemeral sandbox

Create an [ephemeral sandbox](https://www.daytona.io/docs/en/sandboxes/#ephemeral-sandboxes) that is automatically deleted when stopped.

```typescript
import { Daytona } from '@daytona/sdk';

const daytona = new Daytona();
const sandbox = await daytona.v1.create({
    ephemeral: true,
    autoStopInterval: 5
});
```

### Create a sandbox from a snapshot

Create a sandbox from a [snapshot](https://www.daytona.io/docs/en/snapshots/).

```typescript
import { Daytona } from '@daytona/sdk';

const daytona = new Daytona();
const sandbox = await daytona.v1.create({
    snapshot: 'my-snapshot-name',
    language: 'typescript'
});
```

### Execute commands

Execute commands in the sandbox.

```typescript
// Execute a shell command
const response = await sandbox.process.executeCommand('echo "Hello, World!"')
console.log(response.result)

// Run TypeScript code
const response = await sandbox.process.codeRun(`
const x = 10
const y = 20
console.log(\`Sum: \${x + y}\`)
`)
console.log(response.result)
```

### File operations

Upload, download, and search files in the sandbox.

```typescript
// Upload a file
await sandbox.fs.uploadFile(Buffer.from('Hello, World!'), 'path/to/file.txt')

// Download a file
const content = await sandbox.fs.downloadFile('path/to/file.txt')

// Search for files
const matches = await sandbox.fs.findFiles(root_dir, 'search_pattern')
```

### Git operations

Clone, list branches, and add files to the sandbox.

```typescript
// Clone a repository
await sandbox.git.clone('https://github.com/example/repo', 'path/to/clone')

// List branches
const branches = await sandbox.git.branches('path/to/repo')

// Add files
await sandbox.git.add('path/to/repo', ['file1.txt', 'file2.txt'])
```

### Language server protocol

Create and start a language server to get code completions, document symbols, and more.

```typescript
// Create and start a language server
const lsp = await sandbox.createLspServer('typescript', 'path/to/project')
await lsp.start()

// Notify the lsp for the file
await lsp.didOpen('path/to/file.ts')

// Get document symbols
const symbols = await lsp.documentSymbols('path/to/file.ts')

// Get completions
const completions = await lsp.completions('path/to/file.ts', {
  line: 10,
  character: 15,
})
```

## List method return shapes

Each `list` method returns a different shape depending on the resource. The table below shows the exact return type and how to access the elements.

| Method | Return type | Shape | Access elements |
| --- | --- | --- | --- |
| `daytona.v1.snapshot.list(page?, limit?)` | `Promise<PaginatedSnapshots>` | Paginated wrapper | `result.items.forEach(...)` |
| `daytona.v1.secret.list(query?)` | `Promise<ListSecretsResponse>` | Cursor-paginated wrapper | `page.items.forEach(...)` |
| `daytona.v1.volume.list()` | `Promise<Volume[]>` | Bare array | `volumes.forEach(...)` |
| `daytona.v1.list(query?)` | `AsyncIterableIterator<Sandbox>` | Lazy iterator | `for await (const s of daytona.v1.list())` |

`PaginatedSnapshots` and `ListSecretsResponse` are **wrapper objects**, not arrays. Calling `.map()` or `.forEach()` directly on them throws `TypeError: result.map is not a function`. Always go through `.items`:

```typescript
// snapshots — page-number pagination
const result = await daytona.v1.snapshot.list(1, 20)
// result.items  → Snapshot[]
// result.total  → number (total across all pages)
// result.page   → number (current page, 1-indexed)
// result.totalPages → number
result.items.forEach(snapshot => console.log(snapshot.name))

// secrets — cursor pagination
let cursor: string | undefined
do {
  const page = await daytona.v1.secret.list({ cursor, limit: 50 })
  // page.items      → Secret[]
  // page.total      → number
  // page.nextCursor → string | null (null = no more pages)
  page.items.forEach(secret => console.log(secret.name))
  cursor = page.nextCursor ?? undefined
} while (cursor)

// volumes — bare array, iterate directly
const volumes = await daytona.v1.volume.list()
volumes.forEach(vol => console.log(vol.name))

// sandboxes — lazy async iterator, fetches pages on demand
for await (const sandbox of daytona.v1.list({ labels: { env: 'dev' } })) {
  console.log(sandbox.id)
}
```
