**Validation messages match across SDKs** — the same misuse now gives the same
message in Python, TypeScript and Go, modulo parameter naming:
- missing or conflicting credentials: `configure api_key or jwt_token (in
  DaytonaConfig or as DAYTONA_API_KEY / DAYTONA_JWT_TOKEN)`, and
  `jwt_token requires project_id (in DaytonaConfig or as DAYTONA_PROJECT_ID)`;
- every invalid duration: `<name> must be a non-negative finite number of
  seconds`;
- `search(states=...)`: a value that is neither a state nor a list is a type
  error (`states must be a sandbox state or a list of sandbox states`; a number
  used to read as an empty list), an empty list says `states must name at
  least one sandbox state`, and an unknown state says `states entries must be
  known sandbox states, got "unknown"`;
- a malformed response field reads `x must be an integer` / `logs is
  required` in Python too, instead of the validation library's
  `Input should be a valid integer` / `Field required`;
- a cancelled file transfer is `file transfer cancelled` in Python (it said
  `upload transfer cancelled` / `download transfer cancelled`);
- an invalid port protocol in Python says `ports[].proto must be 'tcp' or
  'udp'` (it reported a port-range error), and a port out of range says
  `ports[].port must be an integer in 1..65535`;
- TypeScript says `got undefined`, not `got a undefined`, for a missing
  options object, and names `reservationId` for an empty reservation id.
