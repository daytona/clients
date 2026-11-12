**Computer-use integers are 32-bit** — in both SDKs the `x` / `y` coordinates
of points, displays and windows, an accessibility node's bounds (`x`, `y`,
`width` and `height`, which may be negative), and a process status's `Pid` and
`Priority` must be signed 32-bit integers, and display and window `width` /
`height` and a process status's `RestartAttempts` non-negative 32-bit ones, as
the contract types them: a value outside raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field.
