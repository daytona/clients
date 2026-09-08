# Ruby SDK Examples

This directory contains example scripts demonstrating how to use the Daytona Ruby SDK.

## Prerequisites

1. **Environment Variables** - Configure your API credentials using one of these methods:

   **Option A: Using .env files (Recommended)**

   Create a `.env.local` file in the directory where you run your code:

   ```bash
   # Required (choose one authentication method)
   DAYTONA_API_KEY=your-api-key
   # OR
   DAYTONA_JWT_TOKEN=your-jwt-token
   DAYTONA_ORGANIZATION_ID=your-org-id  # required when using JWT token

   # Optional
   DAYTONA_TARGET=us  # defaults to your organization's default region
   ```

   The SDK automatically loads only Daytona-specific variables from `.env` and `.env.local` files in the current working directory, where `.env.local` overrides `.env`. Runtime environment variables always take precedence over `.env` files.

   `DAYTONA_API_URL` is the exception: the API endpoint is never read from `.env` or `.env.local`, because the working directory is not always authored by whoever runs the code. Set it with the `api_url:` argument to `Daytona::Config.new` or in the environment of the process (Option B). A value in a dotenv file is ignored, and reported on stderr if it differs from the endpoint in use.

   **Option B: Export manually**

   ```bash
   export DAYTONA_API_KEY="your-api-key"
   export DAYTONA_API_URL="https://app.daytona.io/api"  # optional, this is the default
   export DAYTONA_TARGET="us"  # optional
   ```

2. **Ruby** - Ensure Ruby is installed (the Nix dev shell `nix develop .#ruby` includes Ruby 3.4.5)

3. **Dev shell setup** - The Nix dev shell `nix develop .#ruby` automatically sets up the Ruby environment with the SDK libraries in your `RUBYLIB` path

## Running Examples

Use the `ruby` command to run any example:

```bash
ruby examples/ruby/<example-folder>/<script>.rb
```

For example:

```bash
ruby examples/ruby/exec-command/exec_session.rb
ruby examples/ruby/lifecycle/lifecycle.rb
ruby examples/ruby/file-operations/main.rb
```

The SDK and all client libraries are loaded from source files at the repo root (`sdk-ruby`, `api-client-ruby`, `toolbox-api-client-ruby`), so any changes you make to the SDK will be reflected immediately when you run examples.

## How It Works

The Nix dev shell `nix develop .#ruby` sets up the following environment variables:

- **`RUBYLIB`** - Includes paths to the SDK and client library source files
- **`BUNDLE_GEMFILE`** - Points to the SDK's Gemfile for dependency management

This allows you to use plain `ruby` commands while still loading everything from source, ensuring all changes are reflected automatically.
