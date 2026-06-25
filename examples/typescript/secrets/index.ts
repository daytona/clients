import { Daytona } from '@daytona/sdk'

async function main() {
  const daytona = new Daytona()

  // Create an organization secret. The plaintext value is stored encrypted and is
  // never returned by the API again. Use `hosts` to restrict where the value may be sent.
  const secretName = `example-api-key-${Date.now()}`
  const secret = await daytona.secret.create({
    name: secretName,
    value: 'sk-ant-example-secret-value',
    description: 'Example secret created by the Daytona SDK',
    hosts: ['api.anthropic.com', '*.anthropic.com'],
  })
  console.log(`Created secret '${secret.name}' (id: ${secret.id})`)
  // The injected env var holds this opaque placeholder, never the plaintext value.
  console.log(`Injected placeholder: ${secret.placeholder}`)

  // List all secrets in the organization
  const secrets = await daytona.secret.list()
  console.log(`Organization has ${secrets.length} secret(s)`)

  // Create a sandbox that mounts the secret as the env var ANTHROPIC_API_KEY.
  // The `secrets` map is { envVarName: existingSecretName }.
  const sandbox = await daytona.create({
    language: 'typescript',
    secrets: { ANTHROPIC_API_KEY: secretName },
  })

  // Inside the sandbox the env var holds the opaque placeholder, never the plaintext.
  // The real value is substituted transparently only on outbound requests to the
  // secret's allowed hosts (here api.anthropic.com / *.anthropic.com).
  const result = await sandbox.process.executeCommand('echo $ANTHROPIC_API_KEY')
  console.log(`ANTHROPIC_API_KEY inside sandbox: ${result.result.trim()}`)

  // Rotate the secret value and narrow its allowed hosts. Omitted fields are unchanged.
  const updated = await daytona.secret.update(secret.id, {
    value: 'sk-ant-rotated-value',
    hosts: ['api.anthropic.com'],
  })
  console.log(`Updated secret '${updated.name}'; allowed hosts: ${updated.hosts.join(', ')}`)

  // Cleanup
  await daytona.delete(sandbox)
  await daytona.secret.delete(secret.id)
  console.log('Deleted sandbox and secret')
}

main()
