**Migration tool (Python): `timeout=None` suggestions** — a 0.x lifecycle call
or wait with `timeout=None` (wait without a limit) is suggested with
`timeout=0`, which waits without a deadline in v2 (`delete` keeps its 60-second
default), where the suggestion spelled a conditional on `None`.
