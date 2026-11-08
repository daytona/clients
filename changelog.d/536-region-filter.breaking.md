**CLI: `--region` filters `sandbox list` and `image list`** — with
`--region R` they print only the sandboxes whose `region_id`, or the images
whose `region_id` or `region_ids`, is R or a region inside it. Before, the
flag only chose which API to ask, so regions sharing an API all came back. A
page can now hold fewer items than `--limit`; the cursor still advances over
the API's page.
