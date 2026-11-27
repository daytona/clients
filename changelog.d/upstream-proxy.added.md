**Upstream proxy in network policies** — a network policy can send a
sandbox's web egress through your HTTP or HTTPS forward proxy:
`upstreamProxy: { url, credentialsSecret? }` (TypeScript),
`upstream_proxy={"url": …, "credentials_secret": …}` (Python) and
`NetworkPolicySpec.UpstreamProxy` (Go), on create and on
`updateNetworkPolicy` / `update_network_policy` / `UpdateNetworkPolicy`.
`credentialsSecret` names a project secret holding `username:password`; its
value never enters the sandbox. The policy read back carries the proxy as
`upstreamProxy` / `upstream_proxy` / `UpstreamProxy`, and its `version` is 3.
