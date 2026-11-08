**SDK-owned, read-only models** — public models no longer expose generated
wire types. Python models are frozen, keyword-only dataclasses with tuples and
timezone-aware `datetime` values; TypeScript models are readonly camelCase
interfaces (for example `Image.sandboxClass` / `layerCount`,
`Secret.allowedDomains`, `ProjectBudget.monthlyLimitCents`, and
`ProjectApiKeyWithContext` → `ProjectApiKey`). Python `Image.source` is one
of the frozen `RegistryImageSource`, `DockerfileImageSource`,
`NamedImageSource` and `ManifestImageSource` (read `image.source.ref`, not
`image.source["ref"]`), with the same `type` discriminator as TypeScript, and
`Volume.options` is a read-only mapping. Removed exports include
`SandboxInfo`, `SandboxStateSnapshot`, `SnapshotDetail`, `ImageListItem`,
`ImageBuildResponse` and the TypeScript DTO aliases; Python now exports
`SandboxState`, `JsonValue`, `CpuArch` and `ImageStatus`. Sandbox metadata is
read-only on the handle (update it through methods such as `set_labels()`,
reload with `refresh_data()`, which takes no arguments); `target`,
`error_reason` / `errorReason` and the toolbox URL getter
(`toolbox_proxy_url` / `toolboxProxyUrl`) are removed (use `region` and
`error`). `access_token` / `accessToken` stays: the sandbox-scoped bearer the
handle authenticates with. Python `get()` gains `access_token=`, a caller
token the handle never replaces, as TypeScript's `accessToken` option. A
sandbox or snapshot class the SDK does not know reads as `"unknown"`, with
the API's value in `raw_sandbox_class` / `rawSandboxClass`; a sandbox response
without a class is Linux. `to_json()` / `toJSON()` return the same camelCase
`SandboxHandleJSON` in both SDKs, with no URL or credential; reconnect with
`get(json["sandboxId"])`. The handle and service classes are built by the SDK
only: TypeScript's constructors refuse callers (get them from the client or
the sandbox), and Python's take `_`-prefixed parameters; Python's model
dataclasses stay constructible, keyword-only (`ImageBuilder`, `ImageRef` and
`SnapshotRef` included). `NAME_LABEL` and `idempotency_key_for_name` /
`idempotencyKeyForName` are no longer exported (named creates are still
get-or-create), and Python `ImageBuilder.render_dockerfile()` is renamed
`to_dockerfile()`.
