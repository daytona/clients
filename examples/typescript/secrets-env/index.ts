/*
 * secrets-env — project secrets and per-sandbox environment variables.
 *
 * Daytona ships two distinct ways to get configuration/credentials into a
 * sandbox at create time. This example demonstrates both and, crucially, how
 * they differ:
 *
 *   1. ENV VARS  (`envVars: { NAME: value }`)
 *      Real values carried verbatim into the sandbox. Readable inside the
 *      sandbox as ordinary environment variables. Use for non-sensitive config
 *      (LOG_LEVEL, FEATURE_FLAGS, an app's own settings).
 *
 *   2. SECRETS   (`daytona.secret.set(name, value)` + `secrets: [name]`)
 *      Project-scoped and write-only. The sandbox sees only a placeholder in
 *      an env var of the same name; the real value is never readable inside the
 *      sandbox and is used only in outgoing requests to the secret's allowed
 *      domains. Use for API keys / tokens you don't want readable from inside
 *      the sandbox.
 *
 * So: env vars are verifiable by reading them inside the sandbox; secrets are
 * not (you see only the placeholder). This example asserts exactly that
 * contrast.
 *
 * Run (from examples):
 *     DAYTONA_API_URL=... DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/secrets-env/index.ts
 */

import type { Sandbox } from '@daytona/sdk/v2'
import { client, section, info, pass, fail, IMAGE, RUN } from '../_common'

async function main() {
  const daytona = client()

  // A project secret is write-only: the API never returns its value.
  const secretName = `EXAMPLE_API_KEY`
  const secretValue = `sk-secret-${RUN}`

  section('secret.set(name, value) — create/update a project secret')
  await daytona.secret.set(secretName, secretValue, {
    // allowedDomains: where the secret's value may be sent
    allowedDomains: ['api.example.com'],
    description: 'demo secret for the secrets-env example',
  })
  info(`set secret ${secretName} (value is write-only; never returned)`)

  section('secret.list() — metadata only, never values')
  const secrets = (await daytona.secret.list()).items
  const found = secrets.find((s) => s.name === secretName)
  info(`listed ${secrets.length} secret(s); ${secretName} present=${!!found}`)

  let sandbox: Sandbox | undefined
  try {
    section('create a sandbox with BOTH env vars and the secret attached')
    sandbox = await daytona.create(IMAGE, {
      name: `secrets-env-${RUN}`,
      wait: 200,
      // Env vars: real values, injected verbatim into the sandbox.
      envVars: {
        APP_ENV: 'production',
        LOG_LEVEL: 'debug',
        GREETING: `hello-${RUN}`,
      },
      // Secrets: attach by name. The sandbox gets a placeholder, not the value.
      secrets: [secretName],
    })
    info(`created sandbox id=${sandbox.id} state=${sandbox.state}`)

    section('env vars ARE readable inside the sandbox (real values)')
    for (const [name, expected] of [
      ['APP_ENV', 'production'],
      ['LOG_LEVEL', 'debug'],
      ['GREETING', `hello-${RUN}`],
    ] as const) {
    const res = await sandbox.process.run(`printf %s "$${name}"`)
    const got = (res.output ?? '').trim()
      if (got === expected) pass(`$${name} = "${got}"`)
      else fail(`$${name} = "${got}" (expected "${expected}")`)
    }

    section('the SECRET is NOT the real value inside the sandbox (placeholder only)')
    const inSandbox = (await sandbox.process.run(`printf %s "$${secretName}"`)).output?.trim() ?? ''
    // The env var exists (so apps can reference it), but it holds an opaque
    // placeholder, not the secret we set. The real value is used only in outgoing requests.
    if (inSandbox && inSandbox !== secretValue) {
      pass(`$${secretName} in the sandbox is a placeholder, not the secret value`)
    } else if (inSandbox === secretValue) {
      fail(`$${secretName} leaked the real secret value into the sandbox!`)
    } else {
      info(`$${secretName} not present inside the sandbox; OK`)
    }
  } finally {
    section('cleanup')
    if (sandbox) {
      try {
        await daytona.delete(sandbox)
        info(`deleted sandbox ${sandbox.id}`)
      } catch (e) {
        info(`delete sandbox failed: ${e}`)
      }
    }
    try {
      await daytona.secret.delete(secretName)
      info(`deleted secret ${secretName}`)
    } catch (e) {
      info(`delete secret failed: ${e}`)
    }
  }
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
