**Undecodable JSON responses** — in both SDKs a successful response that
declares JSON and does not decode (invalid JSON, invalid UTF-8, or the
non-standard constants `NaN`, `Infinity` and `-Infinity`) raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) for every call, whether
or not the call reads the body. Python raised a raw `JSONDecodeError` or
`UnicodeDecodeError` and read the constants as numbers; TypeScript handed such
a body on as a string, refused only where the SDK read it. An empty body and a
body without a JSON content type are read as before; the content type is
matched case-insensitively in both SDKs (`Application/JSON` is JSON). Both
SDKs decode the token-exchange response strictly: invalid UTF-8 or a leading
byte-order mark is "token exchange response is not a JSON object" (Python
stripped the BOM and took the token). An interpreter output that is not JSON,
the constants included, is dropped as before.
