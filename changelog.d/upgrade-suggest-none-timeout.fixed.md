**Migration tool (Python): `timeout=None` suggestions** — `timeout=None` on a
0.x lifecycle call or wait is folded into the value it meant
(`sandbox.wait(until="stopped", timeout=0)`), not a conditional on `None`.
