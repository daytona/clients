**CLI: one acknowledgement shape** — a command with no resource to print
answers with its identifier and a boolean named after the action, e.g.
`{"id": "o1", "deleted": true}`. The identifier fields are: `ref` for
`image`, `snapshot` and `volume delete`, which take an id or a name, send it
as given in one request and repeat it; `id` for `org` and `project delete`, `key`, `org-key`
and `invite revoke`, `service-account delete`, `project service-account
remove` and `org leave`; `user_id` for `member remove` and `project member
remove`; `name` for `secret set` / `delete`; `host` for `registry set` /
`delete`; `project_id` for `project budget remove`; `org_id` for
`billing automatic-top-up delete` and `billing balance-notification delete`.
`secret delete` and `registry delete` printed `{"deleted": "<name>"}`; the
revoke, archive, remove and leave commands printed the id under the action's
name; `org delete`, `project delete`, `project budget remove` and the billing
setting deletes printed nothing with JSON output. `image`, `snapshot` and
`volume delete` printed the argument under `id`, which held a name when one
was given.
