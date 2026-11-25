**Image example cleanup** — the image examples wait on the built image with
`images.get(id, wait="ready")`, delete it also when the build, the wait or the
create fails (finding it by name when the build reply was lost), and retry the
delete while the API still holds the image.
