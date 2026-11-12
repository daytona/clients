**Process terminal size** — a process record's `terminal` columns and rows are read only within
1-65535, the range the toolbox contract now declares (the same range requests already had); a
value outside it is the malformed-response `BadRequestError`.
