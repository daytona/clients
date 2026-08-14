// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.toolbox.client.model.ProcessLogFrame;

/**
 * Ordered event delivered by {@link ProcessHandle#streamLogs(String, ProcessLogListener)}.
 *
 * <p>Use this event model for replay-then-live supervision. For bounded page retrieval, consume
 * {@link io.daytona.toolbox.client.model.ProcessLogPage} from {@code logs} instead.
 */
public final class ProcessLogEvent {
    private final String type;
    private final String cursor;
    private final ProcessLogFrame frame;
    private final io.daytona.toolbox.client.model.Process process;

    ProcessLogEvent(String type, String cursor, ProcessLogFrame frame,
                    io.daytona.toolbox.client.model.Process process) {
        this.type = type;
        this.cursor = cursor;
        this.frame = frame;
        this.process = process;
    }

    /** @return event discriminator: {@code log}, {@code state}, or {@code eof} */
    public String getType() { return type; }
    /** @return cursor to resume after interruption; use {@code "start"} after CURSOR_EXPIRED */
    public String getCursor() { return cursor; }
    /** @return frame for {@code log} events, otherwise {@code null}; inspect {@link #getType()} first */
    public ProcessLogFrame getFrame() { return frame; }
    /** @return record for {@code state} events, otherwise {@code null}; inspect {@link #getType()} first */
    public io.daytona.toolbox.client.model.Process getProcess() { return process; }
}
