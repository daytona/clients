**Toolbox responses are checked** — a toolbox response that breaks the
contract (a missing required field or list, a wrong type, a fractional
integer, a malformed watch event) raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field, instead of being filled with an
empty value, in both SDKs. TypeScript also refuses a negative value where the
API's integer has a minimum of 0, as Python did.
