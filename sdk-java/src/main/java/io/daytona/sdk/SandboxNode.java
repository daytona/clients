// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk;

import io.daytona.api.client.api.SandboxApi;
import io.daytona.api.client.model.GangNodeFailure;
import io.daytona.api.client.model.PortPreviewUrl;
import io.daytona.api.client.model.SshAccessDto;
import io.daytona.sdk.exception.DaytonaException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** A physical member with stable identity and independent process/filesystem routing. */
public final class SandboxNode {
    private final int rank;
    private final String id;
    private final SandboxApi sandboxApi;
    private final io.daytona.toolbox.client.ApiClient toolboxClient;
    private final io.daytona.toolbox.client.api.ProcessApi processApi;
    private final Process process;
    private final FileSystem fs;
    private final List<String> warnings = new ArrayList<>();
    private volatile io.daytona.api.client.model.SandboxNode data;

    SandboxNode(Sandbox parent, io.daytona.api.client.model.SandboxNode data) {
        if (data.getId() == null || data.getId().isEmpty() || ".".equals(data.getId()) || "..".equals(data.getId())) {
            throw new DaytonaException("Sandbox node id must be a non-empty path segment other than . or ..");
        }
        this.rank = data.getRank();
        this.id = data.getId();
        this.sandboxApi = parent.nodeSandboxApi();
        this.toolboxClient = new io.daytona.toolbox.client.ApiClient();
        toolboxClient.setHttpClient(parent.getToolboxApiClient().getHttpClient());
        toolboxClient.addDefaultHeader("Authorization", "Bearer " + parent.getApiKey());
        toolboxClient.addDefaultHeader("X-Daytona-Source", "sdk-java");
        String version = Daytona.class.getPackage().getImplementationVersion();
        if (version == null) version = "dev";
        toolboxClient.addDefaultHeader("X-Daytona-SDK-Version", version);
        toolboxClient.setUserAgent("sdk-java/" + version);
        this.processApi = new io.daytona.toolbox.client.api.ProcessApi(toolboxClient);
        this.process = new Process(processApi, parent::getLanguage, () -> toolboxClient, parent::getApiKey);
        this.fs = new FileSystem(new io.daytona.toolbox.client.api.FileSystemApi(toolboxClient));
        update(data, parent.getToolboxProxyUrl());
    }

    void update(io.daytona.api.client.model.SandboxNode value, String proxy) {
        if (value.getRank() != rank || !id.equals(value.getId())) {
            throw new DaytonaException("Sandbox node identity changed during refresh");
        }
        updateProxy(proxy);
        data = value;
    }

    void updateProxy(String proxy) {
        String encoded = java.net.URLEncoder.encode(id, java.nio.charset.StandardCharsets.UTF_8).replace("+", "%20");
        toolboxClient.setBasePath(proxy.replaceAll("/+$", "") + "/" + encoded);
    }

    synchronized void warn(String warning) { if (!warnings.contains(warning)) warnings.add(warning); }
    public List<String> getWarnings() {
        synchronized (this) { return Collections.unmodifiableList(new ArrayList<>(warnings)); }
    }
    public int getRank() { return rank; }
    public String getId() { return id; }
    public int getCpu() { return data.getCpu(); }
    public int getGpu() { return data.getGpu(); }
    public int getMemory() { return data.getMemory(); }
    public int getDisk() { return data.getDisk(); }
    public String getState() { return data.getState() == null ? null : data.getState().getValue(); }
    public int getIncarnation() { return data.getIncarnation(); }
    public GangNodeFailure getFailure() { return data.getFailure(); }
    public String getFabricAddress() { return data.getFabricAddress(); }
    public Integer getFabricPrefixLength() { return data.getFabricPrefixLength(); }
    public Boolean getFabricHealthy() { return data.getFabricHealthy(); }
    public String getFabricUnhealthySince() { return data.getFabricUnhealthySince(); }
    public Process getProcess() { return process; }
    public FileSystem getFs() { return fs; }
    io.daytona.toolbox.client.ApiClient toolboxClient() { return toolboxClient; }
    io.daytona.toolbox.client.api.ProcessApi processApi() { return processApi; }

    public PortPreviewUrl getPreviewLink(int port) {
        return ExceptionMapper.callMain(() -> sandboxApi.getPortPreviewUrl(id, java.math.BigDecimal.valueOf(port), null));
    }
    public SshAccessDto createSshAccess(Integer expiresInMinutes) {
        return ExceptionMapper.callMain(() -> sandboxApi.createSshAccess(id, null, expiresInMinutes == null ? null : java.math.BigDecimal.valueOf(expiresInMinutes)));
    }
    public SshAccessDto createSshAccess() { return createSshAccess(null); }
    public void revokeSshAccess(String token) {
        ExceptionMapper.runMain(() -> sandboxApi.revokeSshAccess(id, null, token));
    }
}
