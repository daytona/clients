**SDK-owned, read-only models** — public models no longer expose generated
wire types. Python models are frozen, keyword-only dataclasses with tuples and
timezone-aware `datetime` values; TypeScript models are readonly camelCase
interfaces and model classes, frozen at run time with list and search pages
and their arrays (JSON fields such as `Volume.options` at every depth; live
`Sandbox` handles are not frozen). Examples: `Image.sandboxClass` /
`layerCount`, `Secret.allowedDomains`, `ProjectBudget.monthlyLimitCents`, and
`ProjectApiKeyWithContext` → `ProjectApiKey`. Python `Image.source` is one of
the frozen `RegistryImageSource`, `DockerfileImageSource`, `NamedImageSource`
and `ManifestImageSource` (read `image.source.ref`, not
`image.source["ref"]`), with the same `type` discriminator as TypeScript. A
sandbox or snapshot class the SDK does not know reads as `"unknown"`, with the
API's value in `raw_sandbox_class` / `rawSandboxClass`. A lifecycle policy's
actions are plain strings in Python.
