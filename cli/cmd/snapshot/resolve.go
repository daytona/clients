// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package snapshot

import "regexp"

// snapshotIdRegex mirrors the server's uuid.validate() check (RFC 4122 v1-5 +
// the nil UUID) so client and server always agree on what is ID-shaped.
// Stricter than google/uuid's Validate, which accepts dashless, braced, and
// URN forms and all versions — using that looser check here would turn the
// name-fallback path into 400s at the server, because DELETE/activate accept
// UUIDs only.
var snapshotIdRegex = regexp.MustCompile(`^(?i:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|00000000-0000-0000-0000-000000000000)$`)

// isSnapshotId reports whether arg is a canonical RFC 4122 v1-5 UUID or the
// nil UUID. Snapshot IDs are UUIDs, but names may also be UUID-shaped, so
// callers must still fall back to the name-resolving endpoint on a 404.
func isSnapshotId(arg string) bool {
	return snapshotIdRegex.MatchString(arg)
}
