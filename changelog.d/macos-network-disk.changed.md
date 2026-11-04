**macOS starts on any Mac:** a stopped macOS sandbox starts on any Mac of the
organization with room, not only on the Mac that held its disk. The start does
not copy the whole disk to that Mac first. If the platform's storage is
unreachable for a short time, the sandbox's reads wait and do not fail. When the
platform updates a macOS base image, each create from it boots the version that
the region's update selects for it, on any Mac. A Mac that cannot serve disks
takes no new sandbox.
