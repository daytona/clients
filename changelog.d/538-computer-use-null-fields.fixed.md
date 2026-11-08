TypeScript computer-use results accept `null` for optional fields from the
API: an accessibility node with `children: null` no longer throws. Both SDKs
accept a computer-use status with an explicit `cause: null` outside the failed
state, as the API allows.
