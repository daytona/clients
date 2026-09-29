// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;

/**
 * Thrown when a Sandbox was destroyed by spot preemption.
 *
 * <p>Detected client-side while waiting for a sandbox state; subclass of {@link DaytonaException}.
 */
public class DaytonaSpotEvictedException extends DaytonaException {
    public DaytonaSpotEvictedException(String message) {
        super(message);
    }

    public DaytonaSpotEvictedException(String message, Throwable cause) {
        super(message, cause);
    }
}
