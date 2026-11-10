**An open CIDR-only network policy is refused** — a network policy with a
non-empty `allow_cidrs` / `allowCidrs`, no domain list (`allow_domains` /
`allowDomains` omitted or null; an empty list `[]` is a domain list) and `block_all` /
`blockAll` not `true` is refused with `BadRequestError` before any request, on
create and on `update_network_policy()` / `updateNetworkPolicy()`: without a
domain list the API leaves the policy open and ignores the CIDR list, so the
sandbox had unrestricted egress while the call read like an allow list. Set
`block_all=True` / `blockAll: true` or `allow_domains` / `allowDomains`.
