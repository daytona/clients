**CLI: an invalid API URL is a usage error** — an API URL (other than a blank
or slash-only value, which counts as unset) that is not an absolute `http://`
or `https://` URL now exits 2 instead of 1.
