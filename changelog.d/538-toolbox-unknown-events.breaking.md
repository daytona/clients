**Unknown stream events are skipped** — a process log stream or watch skips an
event it does not know (whatever its data) and raises the malformed-response
error for a known event whose data is not JSON; Python ended the stream with a
`JSONDecodeError`.
