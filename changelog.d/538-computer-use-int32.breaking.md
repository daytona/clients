**Computer-use integers are 32-bit** — in both SDKs the coordinates of points,
displays, windows and accessibility bounds, and a process status's `Pid` and
`Priority`, must be signed 32-bit integers, and display and window widths and
heights and a process status's `RestartAttempts` non-negative ones, as the
contract types them: a value outside raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field.
