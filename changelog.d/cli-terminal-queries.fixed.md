**CLI: query-free terminal startup** — Commands and interactive prompts no
longer query the terminal background or cursor position, avoiding startup
delays and lost input on terminals that do not answer those queries.
Bracketed paste is ignored by prompts instead of activating choices.
