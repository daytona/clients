// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// NEXT_STANDALONE=1 builds `output: 'standalone'`. NEXT_OTEL_INCLUDES=1 adds the
// outputFileTracingIncludes that the SDK's otelEnabled error message documents.
const config = {}
if (process.env.NEXT_STANDALONE === '1') config.output = 'standalone'
if (process.env.NEXT_OTEL_INCLUDES === '1') {
  config.outputFileTracingIncludes = {
    '/**': [
      './node_modules/{@grpc,@js-sdsl,@opentelemetry,@protobufjs}/**/*',
      './node_modules/{acorn,acorn-import-attributes,cjs-module-lexer,es-module-lexer,forwarded-parse}/**/*',
      './node_modules/{import-in-the-middle,module-details-from-path,require-in-the-middle,lodash.camelcase}/**/*',
      './node_modules/{debug,ms,long,protobufjs,yaml,yargs,yargs-parser,y18n,cliui,escalade}/**/*',
      './node_modules/{get-caller-file,require-directory,string-width,strip-ansi,wrap-ansi}/**/*',
      './node_modules/{ansi-regex,ansi-styles,color-convert,color-name,emoji-regex,is-fullwidth-code-point}/**/*',
    ],
  }
}
module.exports = config
