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
without its required `class` is a `BadRequestError`. A lifecycle policy's
actions are plain strings in Python. `to_json()` / `toJSON()` return the same
camelCase `SandboxHandleJSON` in both SDKs, byte for byte, with no URL or
credential: timestamps are the server's strings, verbatim, and integral sizes
are integers. A handle built from a create response holds only what the
response carries, plus an `img_…` / `snap_…` id the caller passed (a name is
never recorded as an id), and its class only when the call fixes it
(`create_macos` / `createMacos`); Python used to record `linux` and a name as
an image or snapshot id. A create or sandbox response without its `id` is a
`BadRequestError`. Reconnect with `get(json["sandboxId"])`. The TypeScript handle's
`id` and its `image` / `snapshot` references are read-only at run time too. The handle and service classes are built by the SDK
only: TypeScript's constructors refuse callers (get them from the client or
the sandbox, and their published declarations carry no wire, axios or
transport types), and Python's take `_`-prefixed parameters; Python's model
dataclasses stay constructible, keyword-only (`ImageBuilder`, `ImageRef` and
`SnapshotRef` included). `NAME_LABEL` and `idempotency_key_for_name` /
`idempotencyKeyForName` are no longer exported (named creates are still
get-or-create), `images.build()` refuses anything but an `ImageBuilder` with
`BadRequestError` (Python raised `TypeError`), and Python
`ImageBuilder.render_dockerfile()` is renamed
`to_dockerfile()`.
