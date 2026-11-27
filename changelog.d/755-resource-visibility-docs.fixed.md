**Visible resources docs** — Python, TypeScript and Go: `nproc` reports the CPU
limit (`cpu_max * 2` vCPUs), while `/proc/meminfo` shows the memory present now,
which can grow toward `memory_max`; the docs no longer say `/proc/meminfo`
reports the memory limit.
