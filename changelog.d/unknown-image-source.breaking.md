**Image sources of a kind the SDK does not know** — an image whose `source.type`
is a kind this release does not know (one a later API adds) now reads as
Python `UnknownImageSource(raw_type=…)` / TypeScript
`{ type: 'unknown', rawType }`, so an additive API change no longer breaks image
reads: TypeScript threw `BadRequestError` and Python raised a raw `ValueError`
from the response parser. An exhaustive match over `ImageSource` gains this
case. A known source type missing its fields, or a `type` that is not a
non-empty string, is still the malformed-response `BadRequestError` (Python
included, no more raw `ValueError`).
