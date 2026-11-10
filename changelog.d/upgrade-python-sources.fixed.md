**Migration tool (Python): `.pyw` sources and unknown extras** —
`uvx daytona-upgrade` migrates `.pyw` files like `.py` ones, where it skipped
them without an item, and drops an extra of a 0.x Daytona dependency that the
2.x `daytona` package does not define (it keeps `otel`), reporting it as a
manual item, where it copied it onto the new requirement.
