// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

/**
 * Optional server-side filters for {@link Process#list(ProcessListFilter)}.
 *
 * <p>Use this instead of unfiltered {@link Process#list()} when only a process subset is needed;
 * combine fields to narrow the same query rather than filtering records client-side.
 */
public class ProcessListFilter {
    private String state;
    private String kind;
    private String sessionId;
    private String name;
    private Integer pid;

    /** @return lifecycle-state filter; use {@link #getKind()} for process type */
    public String getState() { return state; }
    /** @param state {@code running}, {@code terminal}, or {@code all}; omit to include every state @return this instance */
    public ProcessListFilter setState(String state) { this.state = state; return this; }
    /** @return process-kind filter; use {@link #getState()} for lifecycle status */
    public String getKind() { return kind; }
    /** @param kind {@code exec}, {@code pty}, or {@code code}; omit to include every kind @return this instance */
    public ProcessListFilter setKind(String kind) { this.kind = kind; return this; }
    /** @return session filter; use {@link #getName()} for a caller-assigned process name */
    public String getSessionId() { return sessionId; }
    /** @param sessionId managed-session identifier; omit for processes from all sessions @return this instance */
    public ProcessListFilter setSessionId(String sessionId) { this.sessionId = sessionId; return this; }
    /** @return caller-assigned name filter; use {@link #getPid()} for the OS identifier */
    public String getName() { return name; }
    /** @param name process name assigned at start; omit when names are irrelevant @return this instance */
    public ProcessListFilter setName(String name) { this.name = name; return this; }
    /** @return operating-system PID filter; use {@link #getName()} for stable caller naming */
    public Integer getPid() { return pid; }
    /** @param pid operating-system process ID; prefer name or process ID when reconnecting later @return this instance */
    public ProcessListFilter setPid(Integer pid) { this.pid = pid; return this; }
}
