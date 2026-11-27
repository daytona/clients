**Uploading a local directory is refused** — `upload_file()` / `uploadFile()` /
`UploadFile()` with a directory (or any other non-regular file) as the local
path raise `BadRequestError` ("local_path must be a regular file" /
"localPath must be a regular file") before any request. TypeScript used to
fail inside the request with `ConnectionError: fetch failed`, and Go's message
named an "upload source".
