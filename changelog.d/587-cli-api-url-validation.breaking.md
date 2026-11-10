**CLI: an invalid API URL is a usage error** — an API URL (other than a blank
or slash-only value, which counts as unset) now exits 2 instead of 1 unless it
is an absolute `http://` or `https://` URL with a host, no user info, no query
(not even an empty `?`) and no fragment, e.g. `https://api.example/?x=1` is
refused.
