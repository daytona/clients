**Writing git config with a sandbox token needs `toolbox:exec`** — a sandbox
access token granting `toolbox:git` but not `toolbox:exec` (or `toolbox`) can
no longer write git config: `git.set_config()` / `git.setConfig()` get
`ForbiddenError` (403). Reading it with `git.get_config()` / `git.getConfig()`
and the other git operations still work. Mint the token with `toolbox:exec` as
well when the holder must set git config.
