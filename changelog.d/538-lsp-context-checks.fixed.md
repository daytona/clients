**LSP completion input checks** — Python checks an LSP completion context's
`trigger_kind` (an integer) and `trigger_character` (a string), and refuses
unknown keys in a `position=` mapping, as TypeScript does.
