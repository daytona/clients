{
  description = "Daytona clients development environments (SDKs, generated API clients, CLI, examples)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # macOS Apple SDK — needed for CGO (Go), native gems (Ruby), crypto libs.
        darwinDeps = pkgs.lib.optionals pkgs.stdenv.isDarwin [
          pkgs.apple-sdk
          (pkgs.darwinMinVersionHook "11.0")
        ];

        # Yarn 4 via the corepack bundled with Node (package.json pins yarn@4.13.0).
        yarnWrapper = pkgs.writeShellScriptBin "yarn" ''
          exec ${pkgs.nodejs_22}/bin/corepack yarn "$@"
        '';

        commonPkgs = with pkgs; [ git curl jq gnumake pkg-config ];

        # ── Auto-bootstrap ───────────────────────────────────────────────
        # Each devShell installs its language's project deps on first entry so
        # plain `nix develop` leaves you fully ready. Idempotent: a per-language
        # marker (node_modules / .venv / .bundle) makes re-entry instant. Set
        # DAYTONA_NO_BOOTSTRAP=1 to skip (e.g. CI / quick one-off commands).
        bootstrap = name: marker: cmd: ''
          if [ -z "''${DAYTONA_NO_BOOTSTRAP:-}" ] && [ ! -e "$PWD/${marker}" ]; then
            echo "nix(daytona): first-time setup — ${name}…"
            ${cmd} || echo "nix(daytona): ${name} failed; run it manually"
          fi
        '';

        # Go — cli, sdk-go, api-client-go, toolbox-api-client-go (see go.work).
        goPkgs = with pkgs; [ go_1_25 golangci-lint gopls libgit2 ] ++ darwinDeps;
        goShellHook = ''
          unset GOROOT
          export GOPATH="''${GOPATH:-$HOME/go}"
          export GOBIN="$GOPATH/bin"
          export PATH="$GOBIN:$PATH"
          if [ -z "''${DAYTONA_NO_BOOTSTRAP:-}" ]; then go work sync 2>/dev/null || true; fi
        '';

        # Node / TypeScript — sdk-typescript, api-client(s), cli MCP, examples.
        nodePkgs = [ pkgs.nodejs_22 yarnWrapper ];
        nodeShellHook = ''
          export NX_DAEMON=true
          export NODE_ENV=development
          export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
          export COREPACK_HOME="''${COREPACK_HOME:-$HOME/.cache/corepack}"
          mkdir -p "$COREPACK_HOME"
          ${bootstrap "yarn install (TypeScript)" "node_modules" "yarn install"}
        '';

        # Python — sdk-python, api-client-python(-async), examples/python.
        pythonPkgs = with pkgs; [ python312 poetry ];
        pythonShellHook = ''
          export POETRY_VIRTUALENVS_IN_PROJECT=true
          ${bootstrap "poetry install (Python)" ".venv" "poetry install"}
          [ -d "$PWD/.venv/bin" ] && export PATH="$PWD/.venv/bin:$PATH"
        '';

        # Ruby — sdk-ruby, api-client-ruby, toolbox-api-client-ruby (root Gemfile path gems).
        rubyPkgs = with pkgs; [ ruby_3_4 ] ++ darwinDeps;
        rubyShellHook = ''
          export RUBYLIB="$PWD/sdk-ruby/lib:$PWD/api-client-ruby/lib:$PWD/toolbox-api-client-ruby/lib"
          export BUNDLE_GEMFILE="$PWD/Gemfile"
          export BUNDLE_PATH="$PWD/.bundle"
          # api-client-ruby reaches the API through typhoeus/ethon, which dlopen()
          # libcurl via FFI at runtime. Nix shells expose no system libs on the loader
          # path, so make libcurl (+ libstdc++ for native gem extensions) discoverable.
          export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.curl pkgs.stdenv.cc.cc.lib ]}:''${LD_LIBRARY_PATH:-}"
          ${bootstrap "bundle install (Ruby)" ".bundle" "bundle install"}
        '';

        # Java — sdk-java, api-client-java, toolbox-api-client-java, examples/java (Gradle).
        # No install step: Gradle resolves the SDK via composite build on first `gradle` run.
        javaPkgs = [ pkgs.jdk21 pkgs.gradle ];
        javaShellHook = ''
          export JAVA_HOME="${pkgs.jdk21.home}"
        '';
      in
      {
        devShells = {
          # Everything — all client toolchains; `nix develop` bootstraps every language.
          default = pkgs.mkShell {
            name = "daytona-clients";
            packages = commonPkgs ++ goPkgs ++ nodePkgs ++ pythonPkgs ++ rubyPkgs ++ javaPkgs;
            shellHook = ''
              ${goShellHook}
              ${nodeShellHook}
              ${pythonShellHook}
              ${rubyShellHook}
              ${javaShellHook}
              echo "nix(daytona): dev shell ready (go/node/python/ruby/java)."
            '';
          };

          go = pkgs.mkShell {
            name = "daytona-clients-go";
            packages = commonPkgs ++ goPkgs;
            shellHook = goShellHook;
          };

          node = pkgs.mkShell {
            name = "daytona-clients-node";
            packages = commonPkgs ++ nodePkgs;
            shellHook = nodeShellHook;
          };

          python = pkgs.mkShell {
            name = "daytona-clients-python";
            packages = commonPkgs ++ pythonPkgs;
            shellHook = pythonShellHook;
          };

          ruby = pkgs.mkShell {
            name = "daytona-clients-ruby";
            packages = commonPkgs ++ rubyPkgs;
            shellHook = rubyShellHook;
          };

          java = pkgs.mkShell {
            name = "daytona-clients-java";
            packages = commonPkgs ++ javaPkgs;
            shellHook = javaShellHook;
          };
        };
      }
    );
}
