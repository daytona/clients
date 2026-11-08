**CLI: JSON output is the API's representation** — commands that show an API
resource print the JSON the API returned, and lists print each item as the API
returned it: fields a newer API adds appear, nulls stay null, and fields the
API leaves out are absent instead of zero values (`"cpu": 0`,
`"created_at": "0001-01-01T00:00:00Z"`). Toolbox-derived output is the
toolbox's JSON in snake_case. `version -o json` omits the API fields it could
not read instead of printing empty strings.
