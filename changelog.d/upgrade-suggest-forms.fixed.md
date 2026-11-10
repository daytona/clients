**Migration tool: suggestions use the call's own arguments in more forms** —
`timeout=None` on a 0.x lifecycle call or wait is folded into the value it meant
(`sandbox.wait(until="stopped", timeout=0)`, not a conditional on `None`), and
the computer-use keyboard forms (`type`, `press`, `hotkey`) carry the call's
own text, keys and delay, converted from milliseconds, where they named
placeholder variables. Notes name the exported computer-use result classes
(`ComputerUseScreenshotResponse`, …). Both tools write the same forms.
