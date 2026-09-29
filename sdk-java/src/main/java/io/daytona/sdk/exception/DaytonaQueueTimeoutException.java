// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;

/**
 * Thrown when a Sandbox was destroyed by the API because it waited longer than its queue timeout
 * for a runner.
 */
public class DaytonaQueueTimeoutException extends DaytonaTimeoutException {
    public DaytonaQueueTimeoutException(String message) {
        super(message);
    }

    public DaytonaQueueTimeoutException(String message, Throwable cause) {
        super(message, cause);
    }

    public DaytonaQueueTimeoutException(int statusCode, String message, String code, String source) {
        super(statusCode, message, code, source);
    }

    public DaytonaQueueTimeoutException(int statusCode, String message, Throwable cause, String code, String source) {
        super(statusCode, message, cause, code, source);
    }

    public DaytonaQueueTimeoutException(String message, String code, String source) {
        super(message, code, source);
    }

    public DaytonaQueueTimeoutException(String message, Throwable cause, String code, String source) {
        super(message, cause, code, source);
    }
}
