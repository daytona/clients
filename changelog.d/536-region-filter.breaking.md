**CLI: `--region` filters `sandbox list` and `image list`** — with
`--region R` they ask every API that serves R or a region inside it and print
only the sandboxes whose `region_id`, or the images whose `region_id` or
`region_ids`, is R or a region inside it. Before, the flag only chose R's own
API, so regions sharing it all came back and regions inside R with their own
API were missed. A page can now hold fewer items than `--limit`; the cursor
still advances over each API's page.
