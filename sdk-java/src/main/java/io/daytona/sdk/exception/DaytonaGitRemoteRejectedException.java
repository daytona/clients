// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.exception;
/**
 * The git remote rejected the operation (hooks, branch protection or quota).
 *
 * <p>Subclass of {@link DaytonaUnprocessableEntityException}.
 */
public class DaytonaGitRemoteRejectedException extends DaytonaUnprocessableEntityException {
    public DaytonaGitRemoteRejectedException(String message) {
        super(message);
    }

    public DaytonaGitRemoteRejectedException(String message, Throwable cause) {
        super(message, cause);
    }

    public DaytonaGitRemoteRejectedException(String message, String code, String source) {
        super(message, code, source);
    }

    public DaytonaGitRemoteRejectedException(String message, Throwable cause, String code, String source) {
        super(message, cause, code, source);
    }
}
