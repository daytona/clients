// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

/**
 * Initial terminal settings for a PTY-only managed process.
 *
 * <p>Use this with {@link ProcessStartOptions#setKind(String)} set to {@code pty}. Do not use it
 * for exec/code processes; resize an already running PTY with {@link ProcessHandle#resize(int, int)}.
 */
public class ProcessTerminalOptions {
    private Integer cols;
    private Integer rows;
    private String term;

    /** @return initial terminal columns, or {@code null}; use handle resize after start */
    public Integer getCols() { return cols; }
    /** @param cols initial PTY columns; use handle resize for later changes @return this instance */
    public ProcessTerminalOptions setCols(Integer cols) { this.cols = cols; return this; }
    /** @return initial terminal rows, or {@code null}; use handle resize after start */
    public Integer getRows() { return rows; }
    /** @param rows initial PTY rows; use handle resize for later changes @return this instance */
    public ProcessTerminalOptions setRows(Integer rows) { this.rows = rows; return this; }
    /** @return PTY terminal type, or {@code null}; dimensions are exposed separately */
    public String getTerm() { return term; }
    /** @param term PTY terminal type such as {@code xterm-256color}; omit for daemon default @return this instance */
    public ProcessTerminalOptions setTerm(String term) { this.term = term; return this; }
}
