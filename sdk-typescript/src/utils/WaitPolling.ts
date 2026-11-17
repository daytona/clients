/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

// State events drive waits; polling is only a safety net, so keep it sparse while events flow.
export const EVENT_BACKED_POLL_INTERVAL_MS = 5000
// Subscribed but the connection is down: stay at 1 s so a gateway outage does not multiply GET load.
export const SUBSCRIBED_POLL_INTERVAL_MS = 1000
// Spreads the polls of concurrent waits apart.
export const POLL_JITTER = 0.2

/** @internal Delay before a wait's next poll; without a subscription the fast cadence applies unchanged. */
export function waitPollIntervalMs(
  subscribed: boolean,
  connected: boolean,
  fastIntervalMs: number,
  random: () => number = Math.random,
): number {
  if (!subscribed) return fastIntervalMs
  const base = connected ? EVENT_BACKED_POLL_INTERVAL_MS : SUBSCRIBED_POLL_INTERVAL_MS
  return base * (1 + POLL_JITTER * (2 * random() - 1))
}
