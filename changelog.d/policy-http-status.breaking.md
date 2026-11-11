**No `http_status` on network policy and sandbox secrets results** —
`NetworkPolicyInfo` and `SandboxSecretsInfo` drop Python's `http_status` and
TypeScript's `httpStatus`: the HTTP status of the response (200 or 202) is not
part of the result; read `status` for the policy's or secrets' state.
