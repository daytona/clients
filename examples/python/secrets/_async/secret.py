import asyncio
import time

from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams, CreateSecretParams, UpdateSecretParams


async def main():
    async with AsyncDaytona() as daytona:
        # Create an organization secret. The plaintext value is stored encrypted and is
        # never returned by the API again. Use `hosts` to restrict where the value may be sent.
        secret_name = f"example-api-key-{int(time.time())}"
        secret = await daytona.secret.create(
            CreateSecretParams(
                name=secret_name,
                value="sk-ant-example-secret-value",
                description="Example secret created by the Daytona SDK",
                hosts=["api.anthropic.com", "*.anthropic.com"],
            )
        )
        print(f"Created secret '{secret.name}' (id: {secret.id})")
        # The injected env var holds this opaque placeholder, never the plaintext value.
        print(f"Injected placeholder: {secret.placeholder}")

        # List all secrets in the organization
        secrets = await daytona.secret.list()
        print(f"Organization has {len(secrets)} secret(s)")

        # Create a sandbox that mounts the secret as the env var ANTHROPIC_API_KEY.
        # The `secrets` map is {env_var_name: existing_secret_name}.
        sandbox = await daytona.create(
            CreateSandboxFromSnapshotParams(
                language="python",
                secrets={"ANTHROPIC_API_KEY": secret_name},
            )
        )

        # Inside the sandbox the env var holds the opaque placeholder, never the plaintext.
        # The real value is substituted transparently only on outbound requests to the
        # secret's allowed hosts (here api.anthropic.com / *.anthropic.com).
        response = await sandbox.process.exec("echo $ANTHROPIC_API_KEY")
        print(f"ANTHROPIC_API_KEY inside sandbox: {response.result.strip()}")

        # Rotate the secret value and narrow its allowed hosts. Omitted fields are unchanged.
        updated = await daytona.secret.update(
            secret.id,
            UpdateSecretParams(value="sk-ant-rotated-value", hosts=["api.anthropic.com"]),
        )
        print(f"Updated secret '{updated.name}'; allowed hosts: {', '.join(updated.hosts)}")

        # Cleanup
        await daytona.delete(sandbox)
        await daytona.secret.delete(secret.id)
        print("Deleted sandbox and secret")


if __name__ == "__main__":
    asyncio.run(main())
