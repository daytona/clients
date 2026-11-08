**CLI: `--file` keys are exact** — a `--file` body's keys must name the API's
request fields exactly, at any depth, on every command; a key that differs
from a field only in case (`{"Name": "x"}`, `CORES_MAX`) is an unknown field
(exit 2), with a hint naming the field. Some commands used to read such a
key as the field and others refused it. Keys of map values such as `env` and
`labels` are names of your own and are sent as given. `image create`'s `source` is checked by its `type`: an unknown type, or a
key the `registry` (`type`, `ref`) or `named` (`type`, `name`) shape does not
have, is refused before anything is sent.
