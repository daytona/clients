// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import java.util.List;
import java.util.Map;

/**
 * Creation options for a background process supervised through {@link ProcessHandle}.
 *
 * <p>Use these with {@link Process#start(ProcessStartOptions)} when the caller owns streaming,
 * stdin, termination, output collection, and cleanup. Use {@link ProcessRunOptions} for a one-shot
 * process whose output should be collected automatically.
 */
public class ProcessStartOptions {
    private List<String> argv;
    private String shellCommand;
    private String shell;
    private Boolean login;
    private String name;
    private String sessionId;
    private String cwd;
    private Map<String, String> env;
    private String user;
    private String stdin;
    private Integer timeoutMs;
    private String kind;
    private ProcessTerminalOptions terminal;
    private String keepLogs;

    /** @return direct argument vector, mutually exclusive with {@link #getShellCommand()} */
    public List<String> getArgv() { return argv; }
    /** @param argv executable and arguments; use shell command only for shell syntax @return this instance */
    public ProcessStartOptions setArgv(List<String> argv) { this.argv = argv; return this; }
    /** @return shell command, mutually exclusive with {@link #getArgv()} */
    public String getShellCommand() { return shellCommand; }
    /** @param shellCommand command requiring shell parsing; prefer argv for direct execution @return this instance */
    public ProcessStartOptions setShellCommand(String shellCommand) { this.shellCommand = shellCommand; return this; }
    /** @return shell selected for a shell command; irrelevant to direct argv execution */
    public String getShell() { return shell; }
    /** @param shell {@code auto}, {@code sh}, {@code bash}, or {@code zsh}; omit for automatic selection @return this instance */
    public ProcessStartOptions setShell(String shell) { this.shell = shell; return this; }
    /** @return whether the selected shell runs in login mode; irrelevant to argv execution */
    public Boolean getLogin() { return login; }
    /** @param login login-shell mode; leave false/null for ordinary shell execution @return this instance */
    public ProcessStartOptions setLogin(Boolean login) { this.login = login; return this; }
    /** @return caller-assigned name used for listing; process ID remains the reconnect key */
    public String getName() { return name; }
    /** @param name discoverable process name; persist returned ID for exact reconnects @return this instance */
    public ProcessStartOptions setName(String name) { this.name = name; return this; }
    /** @return optional grouping session identifier; distinct from the process ID */
    public String getSessionId() { return sessionId; }
    /** @param sessionId grouping identifier for list filters; omit when no grouping is needed @return this instance */
    public ProcessStartOptions setSessionId(String sessionId) { this.sessionId = sessionId; return this; }
    /** @return working directory, or {@code null} for the sandbox default */
    public String getCwd() { return cwd; }
    /** @param cwd process working directory; omit to use the sandbox default @return this instance */
    public ProcessStartOptions setCwd(String cwd) { this.cwd = cwd; return this; }
    /** @return process environment additions; user selection is exposed separately */
    public Map<String, String> getEnv() { return env; }
    /** @param env process environment additions; use cwd/user setters for execution identity/location @return this instance */
    public ProcessStartOptions setEnv(Map<String, String> env) { this.env = env; return this; }
    /** @return operating-system user, or {@code null} for the sandbox default */
    public String getUser() { return user; }
    /** @param user operating-system user; omit to use the sandbox default identity @return this instance */
    public ProcessStartOptions setUser(String user) { this.user = user; return this; }
    /** @return stdin mode; choose {@code pipe} only when handle writes are required */
    public String getStdin() { return stdin; }
    /** @param stdin {@code none} or {@code pipe}; use pipe for {@link ProcessHandle#stdin(String)} @return this instance */
    public ProcessStartOptions setStdin(String stdin) { this.stdin = stdin; return this; }
    /** @return daemon-enforced execution timeout; run wait timeout is configured separately */
    public Integer getTimeoutMs() { return timeoutMs; }
    /** @param timeoutMs process lifetime deadline; use run wait timeout only to bound client waiting @return this instance */
    public ProcessStartOptions setTimeoutMs(Integer timeoutMs) { this.timeoutMs = timeoutMs; return this; }
    /** @return process kind; terminal settings and attachment apply only to {@code pty} */
    public String getKind() { return kind; }
    /** @param kind {@code exec}, {@code pty}, or {@code code}; use pty only for interactive terminals @return this instance */
    public ProcessStartOptions setKind(String kind) { this.kind = kind; return this; }
    /** @return initial PTY settings, or {@code null}; invalid for exec/code processes */
    public ProcessTerminalOptions getTerminal() { return terminal; }
    /** @param terminal initial PTY settings; use handle resize for later changes @return this instance */
    public ProcessStartOptions setTerminal(ProcessTerminalOptions terminal) { this.terminal = terminal; return this; }
    /** @return log-retention policy; {@code start} defaults to retention until cleanup */
    public String getKeepLogs() { return keepLogs; }
    /** @param keepLogs {@code until_cleanup}, {@code on_exit_ttl}, or {@code none}; prefer default until-cleanup for reconnectable starts @return this instance */
    public ProcessStartOptions setKeepLogs(String keepLogs) { this.keepLogs = keepLogs; return this; }
}
