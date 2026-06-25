# frozen_string_literal: true

require 'daytona'

daytona = Daytona::Daytona.new

# Create an organization secret. The plaintext value is stored encrypted and is
# never returned by the API again. Use `hosts` to restrict where the value may be sent.
secret_name = "example-api-key-#{Time.now.to_i}"
secret = daytona.secret.create(
  secret_name,
  'sk-ant-example-secret-value',
  description: 'Example secret created by the Daytona SDK',
  hosts: ['api.anthropic.com', '*.anthropic.com']
)
puts "Created secret '#{secret.name}' (id: #{secret.id})"
# The injected env var holds this opaque placeholder, never the plaintext value.
puts "Injected placeholder: #{secret.placeholder}"

# List all secrets in the organization
secrets = daytona.secret.list
puts "Organization has #{secrets.length} secret(s)"

# Create a sandbox that mounts the secret as the env var ANTHROPIC_API_KEY.
# The `secrets` map is { env_var_name => existing_secret_name }.
sandbox = daytona.create(
  Daytona::CreateSandboxFromSnapshotParams.new(
    language: Daytona::CodeLanguage::PYTHON,
    secrets: { 'ANTHROPIC_API_KEY' => secret_name }
  )
)

# Inside the sandbox the env var holds the opaque placeholder, never the plaintext.
# The real value is substituted transparently only on outbound requests to the
# secret's allowed hosts (here api.anthropic.com / *.anthropic.com).
result = sandbox.process.exec(command: 'echo $ANTHROPIC_API_KEY')
puts "ANTHROPIC_API_KEY inside sandbox: #{result.result.strip}"

# Rotate the secret value and narrow its allowed hosts. Omitted fields are unchanged.
updated = daytona.secret.update(secret.id, value: 'sk-ant-rotated-value', hosts: ['api.anthropic.com'])
puts "Updated secret '#{updated.name}'; allowed hosts: #{updated.hosts.join(', ')}"

# Cleanup
daytona.delete(sandbox)
daytona.secret.delete(secret.id)
puts 'Deleted sandbox and secret'
