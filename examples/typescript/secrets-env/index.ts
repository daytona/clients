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
 *     DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/secrets-env/index.ts
 */

import type { Sandbox } from '@daytona/sdk/v2'
import { client, section, info, pass, fail, RUN, sandboxImage } from '../_common'

async function main(): Promise<boolean> {
  const daytona = client()
  let ok = true

  // A project secret is write-only: the API never returns its value. The name
  // is run-scoped, so the example never touches another secret in the project;
  // it is also the env var the secret is exposed as.
  const secretName = `EXAMPLE_API_KEY_${RUN.toUpperCase()}`
  const secretValue = `sk-secret-${RUN}`

  let secretCreated = false
  let sandbox: Sandbox | undefined
  try {
    section('secret.set(name, value) — create a project secret')
    await daytona.secret.set(secretName, secretValue, {
      // allowedDomains: where the secret's value may be sent
      allowedDomains: ['api.example.com'],
      description: 'demo secret for the secrets-env example',
      // Never overwrite an existing secret: cleanup deletes only ours.
      ifNotExists: true,
    })
    secretCreated = true
    info(`set secret ${secretName} (value is write-only; never returned)`)

    section('secret.iter() — metadata only, never values')
    let found = false
    for await (const s of daytona.secret.iter()) {
      if (s.name === secretName) {
        found = true
        break
      }
    }
    info(`${secretName} present=${found}`)
    if (!found) {
      fail(`secret ${secretName} is missing from secret.iter()`)
      ok = false
    }

    section('create a sandbox with BOTH env vars and the secret attached')
    sandbox = await daytona.create(await sandboxImage(daytona), {
      name: `secrets-env-${RUN}`,
      waitSeconds: 200,
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
      if (res.exitCode === 0 && got === expected) pass(`$${name} = "${got}"`)
      else {
        fail(`$${name} = "${got}" (expected "${expected}", exit=${res.exitCode})`)
        ok = false
      }
    }

    section('the SECRET is NOT the real value inside the sandbox (placeholder only)')
    const res = await sandbox.process.run(`printf %s "$${secretName}"`)
    const inSandbox = res.output?.trim() ?? ''
    // The env var exists (so apps can reference it), but it holds an opaque
    // placeholder, not the secret we set. The real value is used only in outgoing requests.
    if (res.exitCode !== 0) {
      fail(`reading $${secretName} failed (exit=${res.exitCode})`)
      ok = false
    } else if (inSandbox === secretValue) {
      fail(`$${secretName} leaked the real secret value into the sandbox!`)
      ok = false
    } else if (inSandbox) {
      pass(`$${secretName} in the sandbox is a placeholder, not the secret value`)
    } else {
      fail(`$${secretName} is not set inside the sandbox (expected a placeholder)`)
      ok = false
    }
  } finally {
    section('cleanup')
    if (sandbox) {
      try {
        await daytona.delete(sandbox)
        info(`deleted sandbox ${sandbox.id}`)
      } catch (e) {
        fail(`delete sandbox ${sandbox.id} failed: ${e}`)
        ok = false
      }
    }
    if (secretCreated) {
      try {
        await daytona.secret.delete(secretName)
        info(`deleted secret ${secretName}`)
      } catch (e) {
        // A secret left behind in the project fails the example.
        fail(`delete secret ${secretName} failed: ${e}`)
        ok = false
      }
    }
  }

  return ok
}

main().then((ok) => process.exit(ok ? 0 : 1)).catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
