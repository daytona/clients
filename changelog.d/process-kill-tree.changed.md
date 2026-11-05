**Process-tree kills** — On Linux sandboxes with process containment, `kill()`
(`SIGTERM` and `SIGKILL`), `execution_timeout` / `executionTimeout` and deleting
a running process also stop detached descendants (`setsid`, `nohup`, double
fork) and processes left running after their original process exited.
