**List and search arguments are checked** — in both SDKs, before any request,
with `BadRequestError` naming the argument: every list's `cursor` is a string
and its `limit` an integer from 1 to 200 (the contract's range); the search
filters have their contract types: `q`, `name_prefix` / `namePrefix`,
`id_prefix`, `image_ref`, `image_ref_prefix` and `forked_from` are strings,
`region` and `sandbox_class` / `sandboxClass` a string or a list of strings (a
number or a mapping is refused; Python raised a raw `TypeError` or sent a
mapping's keys), `sandbox_class` entries are `linux` or `macos`, `min_cores` /
`max_cores` finite numbers, the MiB bounds integers, `spot` a boolean, `sort`
and `order` the contract's values, and the timestamp bounds either RFC 3339
timestamps without a leap second (year 0001–9999, a real calendar date with
February 29 only in a leap year, hour 00–23, minute and second 00–59, an
optional fraction, and `Z` or an offset within ±23:59), checked and sent
exactly as written, or a `datetime` / `Date`, sent in UTC, so its UTC instant
must fall in years 0001–9999 (a naive `datetime` is taken as UTC; Python
raised a raw `OverflowError` for an instant outside those years);
`mac_models.catalog(region=)` / `macModels.catalog({ region })` takes one
region string (a number was sent, and a list went out differently in each
SDK); `images.list()` / `snapshots.list()` check `name`, `sandbox_id`,
`anonymous` (a boolean; TypeScript `null` is refused, as Python refuses
`None`) and `platform` (`"true"`, `"false"` or `"any"`). A query value with a
lone surrogate (a cursor, a filter, a search list or timestamp) is refused
rather than escaping as a raw `UnicodeEncodeError` or `URIError`. Search
`labels` are sent as JSON, which escapes a lone surrogate, as a request body
does.
