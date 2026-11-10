**CLI: omit an absent profile credential** — `profile list` JSON/YAML rows
omit `credential` when the profile has no saved credential type; previously
`credential` was always present, as an empty string when unset. Scripts reading
`.credential` must handle a missing key.
