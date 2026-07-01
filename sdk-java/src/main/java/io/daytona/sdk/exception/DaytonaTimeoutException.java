// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;

/**
 * Raised when an SDK operation times out.
 *
 * <p>Client-side transport timeouts default to HTTP 408, but mapped HTTP 504
 * (or any server-supplied timeout status) is preserved when available.
 */
public class DaytonaTimeoutException extends DaytonaException {
    public static final int STATUS_CODE = 408;

    /**
     * Creates a timeout exception with a cause.
     *
     * @param message timeout description
     * @param cause root cause
     */
    public DaytonaTimeoutException(String message, Throwable cause) {
        super(message, cause);
    }

    /**
     * Creates a timeout exception.
     *
     * @param message timeout description
     */
    public DaytonaTimeoutException(String message) {
        super(message);
    }

    public DaytonaTimeoutException(int statusCode, String message, String code, String source) {
        super(statusCode, message, code, source);
    }

    public DaytonaTimeoutException(int statusCode, String message, Throwable cause, String code, String source) {
        super(statusCode, message, cause, code, source);
    }

    public DaytonaTimeoutException(String message, String code, String source) {
        super(STATUS_CODE, message, code, source);
    }

    public DaytonaTimeoutException(String message, Throwable cause, String code, String source) {
        super(STATUS_CODE, message, cause, code, source);
    }
}
