**CLI: one acknowledgement shape** — a command with no resource to print
answers with its identifier and a boolean named after the action:
`{"id": "img1", "deleted": true}`. `secret delete` prints
`{"name", "deleted": true}` and `registry delete` `{"host", "deleted": true}`
(they printed `{"deleted": "<name>"}`); `key`, `org-key` and `invite revoke`,
`service-account delete`, `member remove`, `project member remove`,
`project service-account remove` and `org leave` print `{"id" | "user_id",
"revoked" | "archived" | "removed" | "left": true}` (they printed the id under
the action's name). `org delete`, `project delete`, `project budget remove`
and the billing setting deletes, which printed nothing with JSON output, now
acknowledge too.
