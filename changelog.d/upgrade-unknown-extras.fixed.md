**Migration tool (Python): extras the 2.x package does not define** — an extra
of a 0.x Daytona dependency that the 2.x `daytona` package does not define (it
keeps `otel`) is dropped and reported as a manual item, where it was copied onto
the new requirement. A hash-locked record is preserved as before, and the extra
is reported for manual removal.
