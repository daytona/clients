// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

/**
 * Signal and timed-escalation policy for {@link ProcessHandle#kill(ProcessKillOptions)}.
 *
 * <p>Use this instead of {@link ProcessHandle#kill(String)} when a graceful signal should
 * automatically escalate if the process does not terminate.
 */
public class ProcessKillOptions {
    private String signal = "SIGTERM";
    private Integer escalateAfterMs;
    private String escalateTo = "SIGKILL";

    /** @return initial signal, defaulting to SIGTERM before any configured escalation */
    public String getSignal() { return signal; }
    /** @param signal initial graceful signal; call {@code kill(String)} if escalation is unnecessary @return this instance */
    public ProcessKillOptions setSignal(String signal) { this.signal = signal; return this; }
    /** @return escalation delay in milliseconds, or {@code null} when escalation is disabled */
    public Integer getEscalateAfterMs() { return escalateAfterMs; }
    /** @param value delay before escalation; leave {@code null} to send only the initial signal @return this instance */
    public ProcessKillOptions setEscalateAfterMs(Integer value) { this.escalateAfterMs = value; return this; }
    /** @return escalation signal, used only when an escalation delay is configured */
    public String getEscalateTo() { return escalateTo; }
    /** @param value fallback signal after the delay; use {@link #setSignal(String)} for the first signal @return this instance */
    public ProcessKillOptions setEscalateTo(String value) { this.escalateTo = value; return this; }
}
