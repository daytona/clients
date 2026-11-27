**CLI: anonymous images with an idle expiry** — `image create` takes its name
as optional: without one (and without a name in `--file`) it imports an
anonymous image. `image create` and `image capture` take `--idle-expiry`
(a duration in whole seconds, or `max`) for an anonymous image; beside a name
it is a usage error, because a named image is permanent.
