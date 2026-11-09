**A blank or `local` region selects the default region** — on `create*()`,
`create_from_build()` / `createFromBuild()`, `find_by_name()` / `findByName()`
and `images.build()` / `pull()`, a blank or whitespace-only `region`, or
`local` in any letter case and with any surrounding whitespace, selects the
default region and is not sent, neither as the `x-daytona-region` header nor
as the body's `region_id` (the image calls sent a blank value or `local` as
`region_id`, and Python's `find_by_name()` sent a blank region as an empty
`x-daytona-region` header); any other value is sent as given and follows the
header rule, refused with `BadRequestError` before any request. The
reservation calls send `region` as given, because their contract reads a blank
value or `local` as no region.
