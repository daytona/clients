**Sandbox metadata is read-only on the handle** — update it through methods
such as `set_labels()` and reload it with `refresh_data()`, which takes no
arguments. `target`, `error_reason` / `errorReason` and the toolbox URL getter
(`toolbox_proxy_url` / `toolboxProxyUrl`) are removed (use `region` and
`error`); `access_token` / `accessToken` stays, the sandbox-scoped bearer the
handle authenticates with. A sandbox response without its required `class` is
a `BadRequestError`. The TypeScript handle's `id` and its `image` / `snapshot`
references are read-only at run time too, and Python `Sandbox.labels` is a
fresh `dict` on every read (`Mapping` to type checkers): changing it changes
nothing on the handle.
