/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// Every wait outside the events suite exercises the long-poll path: those tests
// drive recorded HTTP fixtures and count the polling loop's requests and
// back-off pauses, and with the hub on the same wait would first try (and fail)
// to open a socket to the fixture's fake host. The hub has its own suite,
// which clears this variable.
process.env.DAYTONA_SANDBOX_EVENTS = 'off'
