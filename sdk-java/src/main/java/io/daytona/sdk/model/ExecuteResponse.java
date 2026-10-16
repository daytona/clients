// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.sdk.model;

import io.daytona.toolbox.client.model.CodeRunResponse;
import io.daytona.toolbox.client.model.CodeRunArtifacts;

public class ExecuteResponse extends io.daytona.toolbox.client.model.ExecuteResponse {
    private CodeRunArtifacts artifacts;

    public ExecuteResponse() {}

    public ExecuteResponse(io.daytona.toolbox.client.model.ExecuteResponse source) {
        super();
        if (source != null) {
            setExitCode(source.getExitCode());
            setResult(source.getResult());
            setStdout(source.getStdout());
            setStderr(source.getStderr());
        }
    }

    public ExecuteResponse(CodeRunResponse source) {
        super();
        if (source != null) {
            setExitCode(source.getExitCode());
            setResult(source.getResult());
            setArtifacts(source.getArtifacts());
        }
    }

    public CodeRunArtifacts getArtifacts() {
        return artifacts;
    }

    public void setArtifacts(CodeRunArtifacts artifacts) {
        this.artifacts = artifacts;
    }

    /**
     * Gets the combined stdout and stderr (interleaved).
     *
     * @return combined stdout and stderr (interleaved)
     */
    @Override
    public String getResult() {
        return super.getResult();
    }

    /**
     * Gets the split stdout stream; null when the sandbox daemon predates split streams.
     *
     * @return split stdout stream, or {@code null}
     */
    @Override
    public String getStdout() {
        return super.getStdout();
    }

    /**
     * Gets the split stderr stream; null when the sandbox daemon predates split streams.
     *
     * @return split stderr stream, or {@code null}
     */
    @Override
    public String getStderr() {
        return super.getStderr();
    }
}
