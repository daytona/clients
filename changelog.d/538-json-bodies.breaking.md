**Undecodable JSON responses** — in both SDKs a successful response that
declares JSON and does not decode (invalid JSON, invalid UTF-8, or the
non-standard constants `NaN`, `Infinity` and `-Infinity`) raises
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) for every call that
buffers its response and decodes it as JSON, whether or not the call uses the
body; raw, streaming and file-transfer responses do not go through the JSON
decoder. Python raised a raw `JSONDecodeError` or `UnicodeDecodeError` and
read the constants as numbers; TypeScript handed such a body on as a string,
refused only where the SDK read it. An empty body and a body without a JSON
content type are read as before; the media type must be exactly
`application/json`, matched case-insensitively with its parameters ignored, in
both SDKs (`Application/JSON` is JSON, `application/jsonl` is not). Both SDKs
decode the token-exchange response strictly: invalid UTF-8 or a leading
byte-order mark is "token exchange response is not a JSON object" (Python
stripped the BOM and took the token). An interpreter output that is not JSON,
the constants included, is dropped as before. JSON may nest arrays and objects
at most 600 deep in both SDKs; an error response declared as JSON and nested
deeper keeps its status's typed error but takes no code or details from the
body, and its message says the body was nested too deeply.
