**Interpreter context identities** — both SDKs refuse a context id or
language that is not a non-empty string with `BadRequestError` when a context
is reconnected, instead of writing a `to_json()` / `toJSON()` identity neither
SDK reads back.
