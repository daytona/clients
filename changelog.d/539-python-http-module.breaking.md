**Python: the `daytona.v2.http` helper module is removed** — it exported four
internal conversion helpers (`gb_to_mib`, `mib_to_gb`, `sandbox_state`,
`snapshot_state`) outside the documented surface (`daytona.v2.__all__`). Sizes
are GiB on the public models and resources, and `Sandbox.state` is already the
SDK state; nothing replaces the module.
