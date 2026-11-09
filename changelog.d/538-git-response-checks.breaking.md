**Git responses are checked** — `git.status()`'s `fileStatus` list, a
`get_config()` / `getConfig()` value (a string or null) and its `scope`
(`'global'` or `'local'`; Python gains the `GitConfigScope` type) are checked
in both SDKs, and `list_branches()` / `listBranches()`, `list_remotes()` /
`listRemotes()` and `glob()` accept only the API's `{ branches }`, `{ remotes
}` and `{ files }` shapes; Python `list_remotes()` and `glob()` returned an
empty list for any other shape. A violation raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field, instead of being filled with an
empty value.
