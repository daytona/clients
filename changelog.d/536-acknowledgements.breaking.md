**CLI: one acknowledgement shape** — a command with no resource to print
answers with its identifier and a boolean named after the action, e.g.
`{"name": "API_KEY", "deleted": true}`. The identifier fields are: `ref` for
`image`, `snapshot` and `volume delete`, which take an id or a name, send it
as given in one request and repeat it (`{"ref": "py312", "deleted": true}`);
`id` for `org` and `project delete`, `key`, `org-key` and `invite revoke`,
`service-account delete`, `project service-account remove` and `org leave`;
`user_id` for `member remove` and `project member remove`; `name` for `secret
delete`; `host` for `registry delete` (`{"host": "ghcr.io", "deleted":
true}`); `project_id` for `project budget remove`; `org_id` for `billing
automatic-top-up delete` and `billing balance-notification delete`. `secret
delete` and `registry delete` printed `{"deleted": "<name>"}`; the revoke,
archive, remove and leave commands printed the id under the action's name;
`org delete`, `project delete`, `project budget remove` and the billing
setting deletes printed nothing with JSON output; `image`, `snapshot` and
`volume delete` printed the argument under `id`, which held a name when one
was given. `secret set` now prints the secret's metadata the API returns, like
`secret get` (it printed `{"name": "API_KEY", "updated": true}`), and
`registry set` the registry credential's metadata, like `registry get` (it
printed `{"host": "ghcr.io", "updated": true}`). They read the stored resource
first, so they need read as well as write access. The `secret` and `registry`
commands never print the secret value or the registry password, even when a
response holds a `value` or `password` field.
