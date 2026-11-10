**CLI: `sandbox cp` never prints a pre-signed URL** — a transfer error no longer
includes the signed URL's query (its signature stays valid until it expires),
and copying a local directory without `-r` fails before anything is signed.
