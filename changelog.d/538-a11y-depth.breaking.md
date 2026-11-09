**Accessibility tree depth cap** — both SDKs read accessibility trees at most
256 levels deep. `a11y.tree()` now asks for 256 levels when `max_depth` /
`maxDepth` is omitted, so a deeper UI comes back cut at that depth with
`truncated` set; an explicit value must be an integer from 0 to 256 (`-1`,
unbounded, is refused) or the call raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) before any request. A tree nested deeper than
requested, or a `find()` match deeper than 256 levels, is that error as a
malformed response, and the SDKs map trees without recursion, so a deeply
nested response can no longer exhaust the stack. Python also raises that
error, instead of a raw `RecursionError`, for an API or toolbox response, a
process log or directory watch event, interpreter output, a bulk-download
error part or the token exchange nested past the JSON decoder's limit. A JSON
response may nest arrays and objects at most 600 levels deep in both SDKs; one
level more is that error ("the JSON is nested too deeply"), whatever the
Python version or the thread's stack size (the decoder's own limit depends on
both).
