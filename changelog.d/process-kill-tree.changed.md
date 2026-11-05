**Process-tree kills** — On Linux sandboxes with process containment, `kill()`
(`SIGTERM` and `SIGKILL`), timeouts and deleting a running process also stop
detached descendants (`setsid`, `nohup`, double fork) and processes left running
after their original process exited.
