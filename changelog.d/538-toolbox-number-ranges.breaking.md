**Toolbox numbers a double cannot carry are refused** — every number field of
a toolbox result (process records, log frames and stream events, watch events,
file info, git, LSP and computer-use results) refuses an integer beyond a
double's range, which overflows to infinity, naming the field ("must be an
integer" / "must be a number"). Python accepted one, such as `1` followed by
400 zeros, which TypeScript reads as `Infinity`, and raised a raw
`OverflowError` for a recording `durationSeconds` or a process `timeoutMs`.
Integers above 2^53 within a double's range stay accepted in both SDKs, except
a process record's `timeoutMs`, which must be an integer up to 2^53 - 1 so its
seconds are exact, and its terminal `cols` / `rows`, which must be int32
values.
