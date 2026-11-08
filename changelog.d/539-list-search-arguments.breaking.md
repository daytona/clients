**List and search arguments are checked** — in both SDKs, before any request,
with `BadRequestError` naming the argument: every list's `cursor` is a string
and its `limit` an integer from 1 to 200 (the contract's range); the search
filters have their contract types: `q`, `name_prefix` / `namePrefix`,
`id_prefix`, `image_ref`, `image_ref_prefix` and `forked_from` are strings,
`region` and `sandbox_class` / `sandboxClass` a string or a list of strings (a
number or a mapping is refused; Python raised a raw `TypeError` or sent a
mapping's keys), `sandbox_class` entries are `linux` or `macos`, `min_cores` /
`max_cores` finite numbers, the MiB bounds integers, `spot` a boolean, `sort`
and `order` the contract's values, and the timestamp bounds date-times or RFC
3339 strings naming a real instant: year 0001 or later, a day within its month
(February 29 only in a leap year), hour 00–23, minute and second 00–59 (no
leap second), an optional fraction, and `Z` or an offset within ±23:59;
`images.list()` / `snapshots.list()` check `name`, `sandbox_id`, `anonymous`
(a boolean; TypeScript `null` is refused, as Python refuses `None`) and
`platform` (`"true"`, `"false"` or `"any"`). A query value with a lone
surrogate (a cursor, a filter, a search list or timestamp) is refused rather
than escaping as a raw `UnicodeEncodeError` or `URIError`. Search `labels` are
sent as JSON, which escapes a lone surrogate, as a request body does.
