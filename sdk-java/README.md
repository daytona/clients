# Daytona Java SDK

The official Java SDK for [Daytona](https://daytona.io), a secure and elastic infrastructure for running AI-generated code. Daytona provides full composable computers — [sandboxes](https://www.daytona.io/docs/en/sandboxes/) — that you can manage programmatically using the Daytona SDK.

The SDK provides an interface for sandbox management, file system operations, Git operations, language server protocol support, process and code execution, and computer use. For more information, see the [documentation](https://www.daytona.io/docs/en/java-sdk/).

## Installation

[![Maven Central](https://img.shields.io/maven-central/v/io.daytona/sdk?label=io.daytona%3Asdk)](https://central.sonatype.com/artifact/io.daytona/sdk/versions)

Replace `x.y.z` below with the version shown in the badge above.

Add the dependency using **Gradle**:

```kotlin
dependencies {
    implementation("io.daytona:sdk:x.y.z")
}
```

or using **Maven**:

```xml
<dependency>
  <groupId>io.daytona</groupId>
  <artifactId>sdk</artifactId>
  <version>x.y.z</version>
</dependency>
```

## Get API key

Generate an API key from the [Daytona Dashboard ↗](https://app.daytona.io/dashboard/keys) to authenticate SDK requests and access Daytona services. For more information, see the [API keys](https://www.daytona.io/docs/en/api-keys/) documentation.

## Configuration

Configure the SDK using [environment variables](https://www.daytona.io/docs/en/configuration/#environment-variables) or by passing a [configuration object](https://www.daytona.io/docs/en/configuration/#configuration-in-code):

- `DAYTONA_API_KEY`: Your Daytona [API key](https://www.daytona.io/docs/en/api-keys/)
- `DAYTONA_API_URL`: The Daytona [API URL](https://www.daytona.io/docs/en/tools/api/)
- `DAYTONA_TARGET`: Your target [region](https://www.daytona.io/docs/en/regions/) environment (e.g. `us`, `eu`)

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.DaytonaConfig;

// Initialize with environment variables
Daytona daytona = new Daytona();

// Initialize with configuration object
DaytonaConfig config = new DaytonaConfig.Builder()
    .apiKey("YOUR_API_KEY")
    .apiUrl("YOUR_API_URL")
    .target("us")
    .build();
Daytona daytona = new Daytona(config);
```

## Create a sandbox

Create a sandbox to run your code securely in an isolated environment.

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.Sandbox;
import io.daytona.sdk.model.CreateSandboxFromSnapshotParams;
import io.daytona.sdk.model.ExecuteResponse;

try (Daytona daytona = new Daytona()) {
    CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
    params.setLanguage("python");
    Sandbox sandbox = daytona.create(params);

    ExecuteResponse response = sandbox.process.codeRun("print('Hello World!')");
    System.out.println(response.getResult());

    sandbox.delete();
}
```

## Examples and guides

Daytona provides [examples](https://www.daytona.io/docs/en/getting-started/#examples) and [guides](https://www.daytona.io/docs/en/guides/) for common sandbox operations, best practices, and a wide range of topics, from basic usage to advanced topics, showcasing various types of integrations between Daytona and other tools.

### Create a sandbox with custom resources

Create a sandbox with [custom resources](https://www.daytona.io/docs/en/sandboxes/#resources) (CPU, memory, disk).

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.Image;
import io.daytona.sdk.model.CreateSandboxFromImageParams;
import io.daytona.sdk.model.Resources;

try (Daytona daytona = new Daytona()) {
    CreateSandboxFromImageParams params = new CreateSandboxFromImageParams();
    params.setImage(Image.debianSlim("3.12"));
    params.setResources(new Resources(2, null, 4, 8));
    Sandbox sandbox = daytona.create(params);
}
```

### Create a sandbox from a snapshot

Create a sandbox from a [snapshot](https://www.daytona.io/docs/en/snapshots/).

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.model.CreateSandboxFromSnapshotParams;

try (Daytona daytona = new Daytona()) {
    CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
    params.setSnapshot("my-snapshot-name");
    params.setLanguage("python");
    Sandbox sandbox = daytona.create(params);
}
```

### Execute commands

Execute commands in the sandbox.

```java
// Execute a shell command
ExecuteResponse response = sandbox.process.executeCommand("echo 'Hello, World!'");
System.out.println(response.getResult());

// Run Python code
ExecuteResponse code = sandbox.process.codeRun("print('Sum:', 10 + 20)");
System.out.println(code.getResult());
```

### File operations

Upload, download, and search files in the sandbox.

```java
// Upload a file
sandbox.fs.uploadFile("Hello, World!".getBytes(), "path/to/file.txt");

// Download a file
byte[] content = sandbox.fs.downloadFile("path/to/file.txt");

// Search for files
List<Match> matches = sandbox.fs.searchFiles(rootDir, "search_pattern");
```

### Git operations

Clone, list branches, and get status in the sandbox.

```java
// Clone a repository
sandbox.git.clone("https://github.com/example/repo", "path/to/clone");

// List branches
Map<String, Object> branches = sandbox.git.branches("path/to/repo");

// Get status
GitStatus status = sandbox.git.status("path/to/repo");
```

### Language server protocol

Create and start a language server to get code completions, document symbols, and more.

```java
// Create and start a language server
LspServer lsp = sandbox.createLspServer("typescript", "path/to/project");
lsp.start("typescript", "path/to/project");

// Notify the LSP for a file
lsp.didOpen("typescript", "path/to/project", "path/to/file.ts");

// Get document symbols
List<LspSymbol> symbols = lsp.documentSymbols("typescript", "path/to/project", "path/to/file.ts");

// Get completions
CompletionList completions = lsp.completions("typescript", "path/to/project", "path/to/file.ts", 10, 15);
```

## List method return shapes

Each `list` method returns a different shape depending on the resource. The table below shows the exact return type and how to access the elements.

| Method | Return type | Shape | Access elements |
| --- | --- | --- | --- |
| `daytona.snapshot().list(page, limit)` | `PaginatedSnapshots` | Paginated wrapper | `result.getItems()` |
| `daytona.secret().list()` / `list(query)` | `ListSecretsResponse` | Cursor-paginated wrapper | `page.getItems()` |
| `daytona.volume().list()` | `List<Volume>` | Bare list | iterate directly |
| `daytona.list()` / `list(query)` | `Iterable<Sandbox>` | Lazy iterable | `for (Sandbox s : daytona.list(...))` |
| `daytona.listStream()` / `listStream(query)` | `Stream<Sandbox>` | Java Stream | `.forEach(...)` / `.filter(...)` |

`PaginatedSnapshots` and `ListSecretsResponse` are **wrapper objects**, not lists. Calling stream or iteration methods directly on them does not compile. Always use the getter:

```java
import io.daytona.sdk.Daytona;
import io.daytona.sdk.model.PaginatedSnapshots;
import io.daytona.sdk.model.ListSecretsResponse;
import io.daytona.sdk.model.ListSecretsQuery;
import io.daytona.sdk.model.ListSandboxesQuery;

try (Daytona daytona = new Daytona()) {

    // snapshots — page-number pagination
    PaginatedSnapshots result = daytona.snapshot().list(1, 20);
    // result.getItems()      → List<Snapshot>
    // result.getTotal()      → int (total across all pages)
    // result.getPage()       → int (current page, 1-indexed)
    // result.getTotalPages() → int
    for (var snapshot : result.getItems()) {
        System.out.println(snapshot.getName());
    }

    // secrets — cursor pagination
    ListSecretsQuery query = new ListSecretsQuery();
    query.setLimit(50);
    while (true) {
        ListSecretsResponse page = daytona.secret().list(query);
        // page.getItems()      → List<Secret>
        // page.getTotal()      → int
        // page.getNextCursor() → String | null (null = no more pages)
        for (var secret : page.getItems()) {
            System.out.println(secret.getName());
        }
        if (page.getNextCursor() == null) break;
        query.setCursor(page.getNextCursor());
    }

    // volumes — bare list, iterate directly
    for (var vol : daytona.volume().list()) {
        System.out.println(vol.getName());
    }

    // sandboxes — lazy iterable, fetches pages on demand
    ListSandboxesQuery sbQuery = new ListSandboxesQuery();
    for (var sandbox : daytona.list(sbQuery)) {
        System.out.println(sandbox.getId());
    }

    // sandboxes — Stream variant (auto-closes on terminal operation)
    try (var stream = daytona.listStream()) {
        stream.forEach(sb -> System.out.println(sb.getId()));
    }
}
```
