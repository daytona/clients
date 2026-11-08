**Sandbox-token minting falls back per request on 404** — a sandbox-token
mint answered with 404 now falls back to the API credential for that request
only; only 405 and 501 stop the handle from minting.
