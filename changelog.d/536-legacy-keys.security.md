**CLI drops leftover exchanged keys** — project keys that earlier releases
minted from a static credential and cached in `credentials.yaml` are removed
from the file on its next write; they were no longer used.
