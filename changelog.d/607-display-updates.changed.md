**Interpreter display updates replace their output** — an update to a display
(IPython `display(obj, display_id=True)` then `handle.update(obj)`) now
replaces, in place, the outputs that display published earlier in the same
run, so `InterpreterResult.results` holds its last state instead of one entry
per update.
