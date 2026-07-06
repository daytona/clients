import time

from daytona import CreateSandboxFromSnapshotParams, CreateSecretParams, Daytona, UpdateSecretParams


def main():
    daytona = Daytona()

    # Create an organization secret. The plaintext value is stored encrypted and is
    # never returned by the API again. Use `hosts` to restrict where the value may be sent.
    secret_name = f"example-api-key-{int(time.time())}"
    secret = daytona.secret.create(
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

    # List all secrets in the organization, following the pagination cursor page by page
    cursor = None
    while True:
        page = daytona.secret.list(cursor=cursor, limit=100)
        for item in page.items:
            print(f"  - {item.name} (id: {item.id})")
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    print(f"Organization has {page.total} secret(s)")

    # Create a sandbox that mounts the secret as the env var ANTHROPIC_API_KEY.
    # The `secrets` map is {env_var_name: existing_secret_name}.
    sandbox = daytona.create(
        CreateSandboxFromSnapshotParams(
            language="python",
            secrets={"ANTHROPIC_API_KEY": secret_name},
        )
    )

    # Inside the sandbox the env var holds the opaque placeholder, never the plaintext.
    # The real value is substituted transparently only on outbound requests to the
    # secret's allowed hosts (here api.anthropic.com / *.anthropic.com).
    response = sandbox.process.exec("echo $ANTHROPIC_API_KEY")
    print(f"ANTHROPIC_API_KEY inside sandbox: {response.result.strip()}")

    # Rotate the secret value and narrow its allowed hosts. Omitted fields are unchanged.
    updated = daytona.secret.update(
        secret.id,
        UpdateSecretParams(value="sk-ant-rotated-value", hosts=["api.anthropic.com"]),
    )
    print(f"Updated secret '{updated.name}'; allowed hosts: {', '.join(updated.hosts)}")

    # Cleanup
    daytona.delete(sandbox)
    daytona.secret.delete(secret.id)
    print("Deleted sandbox and secret")


if __name__ == "__main__":
    main()
