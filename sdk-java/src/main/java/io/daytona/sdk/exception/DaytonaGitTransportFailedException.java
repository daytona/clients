// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;
/**
 * The git remote was unreachable (DNS, TLS, connection or timeout failure).
 *
 * <p>Subclass of {@link DaytonaBadGatewayException}.
 */
public class DaytonaGitTransportFailedException extends DaytonaBadGatewayException {
    public DaytonaGitTransportFailedException(String message) {
        super(message);
    }

    public DaytonaGitTransportFailedException(String message, Throwable cause) {
        super(message, cause);
    }

    public DaytonaGitTransportFailedException(String message, String code, String source) {
        super(message, code, source);
    }

    public DaytonaGitTransportFailedException(String message, Throwable cause, String code, String source) {
        super(message, cause, code, source);
    }
}
