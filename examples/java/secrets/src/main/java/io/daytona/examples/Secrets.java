// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package io.daytona.examples;

import io.daytona.sdk.Daytona;
import io.daytona.sdk.Sandbox;
import io.daytona.sdk.model.CreateSandboxFromSnapshotParams;
import io.daytona.sdk.model.CreateSecretParams;
import io.daytona.sdk.model.ExecuteResponse;
import io.daytona.sdk.model.Secret;
import io.daytona.sdk.model.UpdateSecretParams;

import java.util.List;
import java.util.Map;

public class Secrets {
    public static void main(String[] args) {
        try (Daytona daytona = new Daytona()) {
            // Create an organization secret. The plaintext value is stored encrypted and is
            // never returned by the API again. Use hosts to restrict where the value may be sent.
            String secretName = "example-api-key-" + System.currentTimeMillis();
            CreateSecretParams createParams = new CreateSecretParams();
            createParams.setName(secretName);
            createParams.setValue("sk-ant-example-secret-value");
            createParams.setDescription("Example secret created by the Daytona SDK");
            createParams.setHosts(List.of("api.anthropic.com", "*.anthropic.com"));
            Secret secret = daytona.secret().create(createParams);
            System.out.println("Created secret '" + secret.getName() + "' (id: " + secret.getId() + ")");
            // The injected env var holds this opaque placeholder, never the plaintext value.
            System.out.println("Injected placeholder: " + secret.getPlaceholder());

            // List all secrets in the organization
            List<Secret> secrets = daytona.secret().list();
            System.out.println("Organization has " + secrets.size() + " secret(s)");

            // Create a sandbox that mounts the secret as the env var ANTHROPIC_API_KEY.
            // The secrets map is { envVarName: existingSecretName }.
            CreateSandboxFromSnapshotParams params = new CreateSandboxFromSnapshotParams();
            params.setLanguage("python");
            params.setSecrets(Map.of("ANTHROPIC_API_KEY", secretName));
            Sandbox sandbox = daytona.create(params);

            try {
                // Inside the sandbox the env var holds the opaque placeholder, never the plaintext.
                // The real value is substituted transparently only on outbound requests to the
                // secret's allowed hosts (here api.anthropic.com / *.anthropic.com).
                ExecuteResponse result = sandbox.process.executeCommand("echo $ANTHROPIC_API_KEY");
                System.out.println("ANTHROPIC_API_KEY inside sandbox: " + result.getResult().trim());

                // Rotate the secret value and narrow its allowed hosts. Null fields are left unchanged.
                UpdateSecretParams updateParams = new UpdateSecretParams();
                updateParams.setValue("sk-ant-rotated-value");
                updateParams.setHosts(List.of("api.anthropic.com"));
                Secret updated = daytona.secret().update(secret.getId(), updateParams);
                System.out.println("Updated secret '" + updated.getName() + "'; allowed hosts: " + updated.getHosts());
            } finally {
                // Cleanup
                sandbox.delete();
                daytona.secret().delete(secret.getId());
                System.out.println("Deleted sandbox and secret");
            }
        }
    }
}
