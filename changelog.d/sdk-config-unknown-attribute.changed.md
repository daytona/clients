**Python: misspelled `DaytonaConfig` fields are refused** — assigning a name
that is not a field of the config (`config.request_timout = 5`) raises
`AttributeError` with a "did you mean 'request_timeout'?" hint instead of
storing an attribute no client reads, and a misspelled constructor keyword
(`DaytonaConfig(request_timout=5)`) gets the same hint in its `TypeError`.
Fields declared by a dataclass subclass of `DaytonaConfig`, and settable
properties a subclass defines, can still be assigned.
