**Accessibility tree depth cap** — both SDKs read accessibility trees at most
256 levels deep. `a11y.tree()` now asks for 256 levels when `max_depth` /
`maxDepth` is omitted, so a deeper UI comes back cut at that depth with
`truncated` set; an explicit value must be an integer from 0 to 256 (`-1`,
unbounded, is refused) or the call raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) before any request. A tree nested deeper than
requested, or a `find()` match deeper than 256 levels, is that error as a
malformed response, and the SDKs map trees without recursion, so a deeply
nested response can no longer exhaust the stack. Python also raises that
error, instead of a raw `RecursionError`, for a response body or interpreter
output nested past the JSON decoder's limit.
