{
  description = "Daytona development environments";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # macOS Apple SDK — provides Security, SystemConfiguration, CoreFoundation, etc.
        # Required for CGO (Go), native gems (Ruby), and crypto libraries.
        # In recent nixpkgs the legacy per-framework imports (darwin.apple_sdk.frameworks.*)
        # have been removed in favor of the unified apple-sdk package.
        darwinDeps = pkgs.lib.optionals pkgs.stdenv.isDarwin [
          pkgs.apple-sdk
          (pkgs.darwinMinVersionHook "11.0")
        ];

        # Yarn 4.x wrapper — delegates to corepack bundled with Node.js
        # The project pins yarn via package.json "packageManager": "yarn@4.13.0"
        yarnWrapper = pkgs.writeShellScriptBin "yarn" ''
          exec ${pkgs.nodejs_22}/bin/corepack yarn "$@"
        '';

        # ──────────────────────────────────────────────
        # Shared packages (included in every shell)
        # ──────────────────────────────────────────────
        commonPkgs = with pkgs; [
          git
          curl
          jq
          gnumake
          pkg-config
        ];

        # ──────────────────────────────────────────────
        # Go toolchain
        # Covers: apps/{cli,daemon,proxy,runner,snapshot-manager,ssh-gateway,otel-collector/exporter}
        #         libs/{sdk-go,api-client-go,common-go,computer-use,toolbox-api-client-go}
        # ──────────────────────────────────────────────
        goPkgs = with pkgs; [
          go_1_25 # pin to 1.25.x — matches go.work (go 1.25.5) and the devcontainer
          # (go feature 1.25.5). The unversioned `go` attr now tracks 1.26, which
          # produces different `gomarkdoc` output for the Go SDK docs.
          golangci-lint
          protobuf # provides protoc
          buf
          protoc-gen-go
          protoc-gen-go-grpc
          libgit2
        ] ++ darwinDeps ++ bpfPkgs;

        goShellHook = ''
          unset GOROOT
          export GOPATH="''${GOPATH:-$HOME/go}"
          export GOBIN="$GOPATH/bin"
          export PATH="$GOBIN:$PATH"

          # Install Go tools not packaged in nixpkgs. Reinstall when missing OR
          # when the cached binary was built with a different Go toolchain than the
          # active one: a tool's output can depend on the Go version it was compiled
          # with (e.g. gomarkdoc embeds go/doc/comment, whose [Type.Field] doc-link
          # rendering changed in Go 1.26), so a stale binary silently desyncs the
          # generated docs from what go.work / CI produce.
          _nix_install_go_tool() {
            local name="$1" pkg="$2" bin
            bin="$(command -v "$name" 2>/dev/null)"
            if [ -z "$bin" ] || [ "$(go version "$bin" 2>/dev/null | awk '{print $2}')" != "$(go env GOVERSION)" ]; then
              echo "nix-shell: installing $name ..."
              go install "$pkg" 2>/dev/null || echo "nix-shell: warning — failed to install $name"
            fi
          }
          _nix_install_go_tool swag      "github.com/swaggo/swag/cmd/swag@v1.16.4"
          _nix_install_go_tool gow       "github.com/mitranim/gow@v0.0.0-20260225145757-ff0f6779ab4c"
          _nix_install_go_tool gomarkdoc "github.com/princjef/gomarkdoc/cmd/gomarkdoc@v1.1.0"
          unset -f _nix_install_go_tool
        '';

        # ──────────────────────────────────────────────
        # eBPF toolchain (Linux only)
        # Covers: libs/netleash — `make generate` runs bpf2go, which compiles the
        # BPF C sources with clang and strips them with llvm-strip. libbpf and the
        # kernel UAPI headers supply <bpf/...> and <linux/...>/<asm/...>.
        # Pinned to LLVM 18 to match the committed generated objects.
        # The header packages go in buildInputs (not packages) so the clang
        # cc-wrapper injects their include dirs via NIX_CFLAGS_COMPILE — this lets
        # `make generate` find the headers without any Makefile changes.
        # ──────────────────────────────────────────────
        bpfPkgs = pkgs.lib.optionals pkgs.stdenv.isLinux [
          pkgs.llvmPackages_18.clang # bpf2go: clang -cc
          pkgs.llvmPackages_18.llvm # bpf2go: llvm-strip
        ];

        bpfHeaderInputs = pkgs.lib.optionals pkgs.stdenv.isLinux [
          pkgs.libbpf # <bpf/bpf_helpers.h>, <bpf/bpf_endian.h>
          pkgs.linuxHeaders # <linux/bpf.h>, <asm/types.h>, ...
        ];

        # ──────────────────────────────────────────────
        # X11 development libraries (Linux only)
        # Covers: libs/computer-use — `go build` compiles github.com/go-vgo/robotgo
        # via CGO, whose Linux build declares `#cgo LDFLAGS: -lX11 -lXtst` and
        # includes <X11/Xlib.h>, <X11/Xutil.h>, <X11/XF86keysym.h> and
        # <X11/extensions/XTest.h>. The devcontainer installs the libx11-dev /
        # libxtst-dev apt packages; these are the nixpkgs equivalents.
        # Like the BPF headers above, they go in buildInputs (not packages) so the
        # cc-wrapper injects the include dirs (NIX_CFLAGS_COMPILE) and library
        # paths/rpath (NIX_LDFLAGS) — no Makefile/cgo changes needed.
        computerUseInputs = pkgs.lib.optionals pkgs.stdenv.isLinux [
          pkgs.libx11 # -lX11; <X11/Xlib.h>, <X11/Xutil.h>, <X11/Xresource.h>
          pkgs.libxtst # -lXtst; <X11/extensions/XTest.h>
          pkgs.libxi # <X11/extensions/XInput.h> (pulled in by XTest.h)
          pkgs.xorgproto # <X11/Xatom.h>, <X11/XF86keysym.h>, <X11/extensions/XI.h>, ...
        ];

        # ──────────────────────────────────────────────
        # Node.js / TypeScript toolchain
        # Covers: apps/{api,dashboard,docs}
        #         libs/{sdk-typescript,api-client,toolbox-api-client,analytics-api-client,runner-api-client,opencode-plugin}
        # ──────────────────────────────────────────────
        nodePkgs = [
          pkgs.nodejs_22
          yarnWrapper
        ];

        nodeShellHook = ''
          export NX_DAEMON=true
          export NODE_ENV=development
          export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
          export COREPACK_HOME="''${COREPACK_HOME:-$HOME/.cache/corepack}"
          mkdir -p "$COREPACK_HOME"
        '';

        # ──────────────────────────────────────────────
        # Python toolchain
        # Covers: libs/{sdk-python,api-client-python,api-client-python-async,toolbox-api-client-python,toolbox-api-client-python-async}
        #         examples/python, guides/python
        # ──────────────────────────────────────────────
        pythonPkgs = with pkgs; [
          python312 # compatible with requires-python ^3.9
          poetry
        ];

        pythonShellHook = ''
          export POETRY_VIRTUALENVS_IN_PROJECT=true

          # The Poetry dev group provides Python tooling not packaged in nixpkgs
          # (e.g. pydoc-markdown, used by `npm run docs` in libs/sdk-python). Its
          # `python` loader imports the editable `daytona` package and all its
          # runtime deps, so the tool and the SDK must live in one venv — bootstrap
          # the in-project venv and put it on PATH so those binaries resolve.
          if [ ! -x "$PWD/.venv/bin/pydoc-markdown" ]; then
            echo "nix-shell: installing Python dev dependencies (poetry install) ..."
            poetry install --no-interaction 2>/dev/null \
              || echo "nix-shell: warning — poetry install failed; run it manually"
          fi
          if [ -d "$PWD/.venv/bin" ]; then
            export PATH="$PWD/.venv/bin:$PATH"
          fi
        '';

        # ──────────────────────────────────────────────
        # Ruby toolchain
        # Covers: libs/{sdk-ruby,api-client-ruby,toolbox-api-client-ruby}
        # ──────────────────────────────────────────────
        rubyPkgs = with pkgs; [
          ruby_3_4 # matches devcontainer 3.4.5
        ] ++ darwinDeps;

        rubyShellHook = ''
          export RUBYLIB="$PWD/libs/sdk-ruby/lib:$PWD/libs/api-client-ruby/lib:$PWD/libs/toolbox-api-client-ruby/lib"
          export BUNDLE_GEMFILE="$PWD/Gemfile"
          export BUNDLE_PATH="$PWD/.bundle"
        '';

        # ──────────────────────────────────────────────
        # Java toolchain
        # Covers: libs/{sdk-java,api-client-java,toolbox-api-client-java}
        #         examples/java
        # ──────────────────────────────────────────────
        javaPkgs = [
          pkgs.jdk21 # matches the devcontainer (java feature 21.0.11, Temurin);
          # source still targets Java 11 and Gradle 8.10 supports JDK 21. The JDK
          # major version affects Javadoc → Markdown output for the Java SDK docs.
          pkgs.gradle
        ];

        javaShellHook = ''
          export JAVA_HOME="${pkgs.jdk21.home}"
        '';

      in
      {
        devShells = {

          # Full monorepo — every language and tool
          default = pkgs.mkShell {
            name = "daytona";
            packages = commonPkgs ++ goPkgs ++ nodePkgs ++ pythonPkgs ++ rubyPkgs ++ javaPkgs;
            buildInputs = bpfHeaderInputs ++ computerUseInputs;
            # bpf2go invokes clang with `-target bpf`; the cc-wrapper's hardening
            # flags (e.g. -fzero-call-used-regs) are unsupported for that target.
            hardeningDisable = [ "all" ];
            shellHook = ''
              ${goShellHook}
              ${nodeShellHook}
              ${pythonShellHook}
              ${rubyShellHook}
              ${javaShellHook}
            '';
          };

          # Go services and libraries only
          go = pkgs.mkShell {
            name = "daytona-go";
            packages = commonPkgs ++ goPkgs;
            buildInputs = bpfHeaderInputs ++ computerUseInputs;
            # bpf2go invokes clang with `-target bpf`; the cc-wrapper's hardening
            # flags (e.g. -fzero-call-used-regs) are unsupported for that target.
            hardeningDisable = [ "all" ];
            shellHook = goShellHook;
          };

          # TypeScript / Node.js apps and libraries only
          node = pkgs.mkShell {
            name = "daytona-node";
            packages = commonPkgs ++ nodePkgs;
            shellHook = nodeShellHook;
          };

          # Python SDKs and libraries only
          python = pkgs.mkShell {
            name = "daytona-python";
            packages = commonPkgs ++ pythonPkgs;
            shellHook = pythonShellHook;
          };

          # Ruby SDKs and libraries only
          ruby = pkgs.mkShell {
            name = "daytona-ruby";
            packages = commonPkgs ++ rubyPkgs;
            shellHook = rubyShellHook;
          };

          # Java SDKs and libraries only
          java = pkgs.mkShell {
            name = "daytona-java";
            packages = commonPkgs ++ javaPkgs;
            shellHook = javaShellHook;
          };
        };
      }
    );
}
