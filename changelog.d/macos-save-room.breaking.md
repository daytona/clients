**macOS persistent sandboxes hold room for their saves:** each persistent macOS
sandbox holds 8192 MiB of its Mac's sandbox disk room beside its disk, for its
saves, from the time a Mac claims it until that Mac lets its disk go. Create
admission, a start, auto-reserve and a reservation purchase count it, and so
does `MacReservation.resources_used.disk_mib` (`resourcesUsed.diskMib` in
TypeScript). A persistent sandbox's disk is at most the model's
`sandbox_disk_mib` less 8192 MiB: 274432 MiB on `m4pro-12c-24g`, 233472 MiB on
`m4pro-12c-48g`, and 285696 MiB on `m5pro-15c-24g`; `m4-10c-16g` takes no
persistent sandbox. Two persistent sandboxes at the default disk still fit one
Mac. A request that does not fit answers `409 NO_FREE_SLOT`, or
`409 NO_MAC_AVAILABLE` when it buys a reservation.
