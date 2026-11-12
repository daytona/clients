**Interpreter display updates replace their output** — a display updated in
place (IPython `display(obj, display_id=True)` then `handle.update(obj)`) is
now one entry of `InterpreterResult.results` holding its last state, instead
of one entry per update.
