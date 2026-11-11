**Absolute filesystem paths** — All sandbox filesystem paths must be strings
starting with `/`; relative paths now raise `BadRequestError` locally. This
includes bulk paths, transfer destinations/sources and pre-signed URLs. Local
transfer paths and git repository paths/pathspecs are unchanged.
