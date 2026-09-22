# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# frozen_string_literal: true

require 'digest'
require 'fileutils'
require 'json'
require 'pathname'
require 'shellwords'

module Daytona
  class Context
    attr_reader :source_path
    attr_reader :archive_path

    # @param source_path [String] The path to the source file or directory
    # @param archive_path [String, nil] The path inside the archive file in object storage
    def initialize(source_path:, archive_path: nil)
      @source_path = source_path
      @archive_path = archive_path
    end
  end

  # Represents an image definition for a Daytona sandbox.
  # Do not construct this class directly. Instead use one of its static factory methods,
  # such as `Image.base()`, `Image.debian_slim()`, or `Image.from_dockerfile()`.
  class Image # rubocop:disable Metrics/ClassLength
    # @return [String, nil] The generated Dockerfile for the image
    attr_reader :dockerfile

    # @return [Array<Context>] List of context files for the image
    attr_reader :context_list

    # Supported Python series
    SUPPORTED_PYTHON_SERIES = %w[3.9 3.10 3.11 3.12 3.13].freeze
    LATEST_PYTHON_MICRO_VERSIONS = %w[3.9.22 3.10.17 3.11.12 3.12.10 3.13.3].freeze

    # @param dockerfile [String, nil] The Dockerfile content
    # @param context_list [Array<Context>] List of context files
    def initialize(dockerfile: nil, context_list: [])
      @dockerfile = dockerfile || ''
      @context_list = context_list
    end

    # Adds commands to install packages using pip
    #
    # @param packages [Array<String>] The packages to install
    # @param find_links [Array<String>, nil] The find-links to use
    # @param index_url [String, nil] The index URL to use
    # @param extra_index_urls [Array<String>, nil] The extra index URLs to use
    # @param pre [Boolean] Whether to install pre-release packages
    # @param extra_options [String] Additional options to pass to pip
    # @return [Image] The image with the pip install commands added
    #
    # @example
    #   image = Image.debian_slim("3.12").pip_install("requests", "pandas")
    def pip_install(*packages, find_links: nil, index_url: nil, extra_index_urls: nil, pre: false, extra_options: '') # rubocop:disable Metrics/ParameterLists
      pkgs = flatten_str_args('pip_install', 'packages', packages)
      return self if pkgs.empty?

      extra_args = format_pip_install_args(find_links:, index_url:, extra_index_urls:, pre:, extra_options:)
      @dockerfile += "RUN python -m pip install #{Shellwords.join(pkgs.sort)}#{extra_args}\n"

      self
    end

    # Installs dependencies from a requirements.txt file
    #
    # @param requirements_txt [String] The path to the requirements.txt file
    # @param find_links [Array<String>, nil] The find-links to use
    # @param index_url [String, nil] The index URL to use
    # @param extra_index_urls [Array<String>, nil] The extra index URLs to use
    # @param pre [Boolean] Whether to install pre-release packages
    # @param extra_options [String] Additional options to pass to pip
    # @return [Image] The image with the pip install commands added
    # @raise [Sdk::Error] If the requirements file does not exist
    #
    # @example
    #   image = Image.debian_slim("3.12").pip_install_from_requirements("requirements.txt")
    def pip_install_from_requirements(requirements_txt, find_links: nil, index_url: nil, extra_index_urls: nil, # rubocop:disable Metrics/ParameterLists
                                      pre: false, extra_options: '')
      requirements_txt = File.expand_path(requirements_txt)
      raise Sdk::Error, "Requirements file #{requirements_txt} does not exist" unless File.exist?(requirements_txt)

      extra_args = format_pip_install_args(find_links:, index_url:, extra_index_urls:, pre:, extra_options:)

      archive_path = ObjectStorage.compute_archive_base_path(requirements_txt)
      @context_list << Context.new(source_path: requirements_txt, archive_path:)
      @dockerfile += "COPY #{archive_path} /.requirements.txt\n"
      @dockerfile += "RUN python -m pip install -r /.requirements.txt#{extra_args}\n"

      self
    end

    # Installs dependencies from a pyproject.toml file
    #
    # @param pyproject_toml [String] The path to the pyproject.toml file
    # @param optional_dependencies [Array<String>] The optional dependencies to install
    # @param find_links [String, nil] The find-links to use
    # @param index_url [String, nil] The index URL to use
    # @param extra_index_url [String, nil] The extra index URL to use
    # @param pre [Boolean] Whether to install pre-release packages
    # @param extra_options [String] Additional options to pass to pip
    # @return [Image] The image with the pip install commands added
    # @raise [Sdk::Error] If pyproject.toml parsing is not supported
    #
    # @example
    #   image = Image.debian_slim("3.12").pip_install_from_pyproject("pyproject.toml", optional_dependencies: ["dev"])
    def pip_install_from_pyproject(pyproject_toml, optional_dependencies: [], find_links: nil, index_url: nil, # rubocop:disable Metrics/MethodLength, Metrics/ParameterLists
                                   extra_index_url: nil, pre: false, extra_options: '')
      data = TOML.load_file(pyproject_toml)
      dependencies = data.dig('project', 'dependencies')

      unless dependencies
        raise Sdk::Error, 'No [project.dependencies] section in pyproject.toml file. ' \
                          'See https://packaging.python.org/en/latest/guides/writing-pyproject-toml ' \
                          'for further file format guidelines.'
      end

      return unless optional_dependencies

      optionals = data.dig('project', 'optional-dependencies')
      optional_dependencies.each do |group|
        dependencies.concat(optionals.fetch(group, []))
      end

      pip_install(*dependencies, find_links:, index_url:, extra_index_urls: extra_index_url, pre:, extra_options:)
    end

    # Adds a local file to the image
    #
    # @param local_path [String] The path to the local file
    # @param remote_path [String] The path to the file in the image
    # @return [Image] The image with the local file added
    #
    # @example
    #   image = Image.debian_slim("3.12").add_local_file("package.json", "/home/daytona/package.json")
    def add_local_file(local_path, remote_path)
      remote_path = "#{remote_path}/#{File.basename(local_path)}" if remote_path.end_with?('/')

      local_path = File.expand_path(local_path)
      archive_path = ObjectStorage.compute_archive_base_path(local_path)
      @context_list << Context.new(source_path: local_path, archive_path: archive_path)
      @dockerfile += "COPY #{archive_path} #{remote_path}\n"

      self
    end

    # Adds a local directory to the image
    #
    # @param local_path [String] The path to the local directory
    # @param remote_path [String] The path to the directory in the image
    # @return [Image] The image with the local directory added
    #
    # @example
    #   image = Image.debian_slim("3.12").add_local_dir("src", "/home/daytona/src")
    def add_local_dir(local_path, remote_path)
      local_path = File.expand_path(local_path)
      archive_path = ObjectStorage.compute_archive_base_path(local_path)
      @context_list << Context.new(source_path: local_path, archive_path: archive_path)
      @dockerfile += "COPY #{archive_path} #{remote_path}\n"

      self
    end

    # Runs commands in the image
    #
    # @param commands [Array<String>] The commands to run
    # @return [Image] The image with the commands added
    #
    # @example
    #   image = Image.debian_slim("3.12").run_commands('echo "Hello, world!"', 'echo "Hello again!"')
    def run_commands(*commands)
      commands.each do |command|
        if command.is_a?(Array)
          escaped = command.map { |c| c.gsub('"', '\\"').gsub("'", "\\'") }
          @dockerfile += "RUN #{escaped.map { |c| "\"#{c}\"" }.join(' ')}\n"
        else
          @dockerfile += "RUN #{command}\n"
        end
      end

      self
    end

    # Sets environment variables in the image
    #
    # @param env_vars [Hash<String, String>] The environment variables to set
    # @return [Image] The image with the environment variables added
    #
    # @example
    #   image = Image.debian_slim("3.12").env({"PROJECT_ROOT" => "/home/daytona"})
    def env(env_vars)
      non_str_keys = env_vars.reject { |_key, val| val.is_a?(String) }.keys
      raise Sdk::Error, "Image ENV variables must be strings. Invalid keys: #{non_str_keys}" unless non_str_keys.empty?

      env_vars.each do |key, val|
        @dockerfile += "ENV #{key}=#{Shellwords.escape(val)}\n"
      end

      self
    end

    # Sets the working directory in the image
    #
    # @param path [String] The path to the working directory
    # @return [Image] The image with the working directory added
    #
    # @example
    #   image = Image.debian_slim("3.12").workdir("/home/daytona")
    def workdir(path)
      @dockerfile += "WORKDIR #{Shellwords.escape(path.to_s)}\n"
      self
    end

    # Sets the entrypoint for the image
    #
    # @param entrypoint_commands [Array<String>] The commands to set as the entrypoint
    # @return [Image] The image with the entrypoint added
    #
    # @example
    #   image = Image.debian_slim("3.12").entrypoint(["/bin/bash"])
    def entrypoint(entrypoint_commands)
      unless entrypoint_commands.is_a?(Array) && entrypoint_commands.all? { |x| x.is_a?(String) }
        raise Sdk::Error, 'entrypoint_commands must be a list of strings.'
      end

      args_str = flatten_str_args('entrypoint', 'entrypoint_commands', entrypoint_commands)
      args_str = args_str.map { |arg| "\"#{arg}\"" }.join(', ') if args_str.any?
      @dockerfile += "ENTRYPOINT [#{args_str}]\n"

      self
    end

    # Sets the default command for the image
    #
    # @param cmd [Array<String>] The commands to set as the default command
    # @return [Image] The image with the default command added
    #
    # @example
    #   image = Image.debian_slim("3.12").cmd(["/bin/bash"])
    def cmd(cmd)
      unless cmd.is_a?(Array) && cmd.all? { |x| x.is_a?(String) }
        raise Sdk::Error, 'Image CMD must be a list of strings.'
      end

      cmd_str = flatten_str_args('cmd', 'cmd', cmd)
      cmd_str = cmd_str.map { |arg| "\"#{arg}\"" }.join(', ') if cmd_str.any?
      @dockerfile += "CMD [#{cmd_str}]\n"
      self
    end

    # Adds arbitrary Dockerfile-like commands to the image
    #
    # @param dockerfile_commands [Array<String>] The commands to add to the Dockerfile
    # @param context_dir [String, nil] The path to the context directory
    # @param strict_context [Boolean] When true, a COPY source that resolves outside context_dir is
    #   rejected instead of read. Requires context_dir, so that the boundary is explicit rather than
    #   taken from the working directory. Sources that legitimately live elsewhere belong in
    #   add_local_file or add_local_dir
    # @return [Image] The image with the Dockerfile commands added
    #
    # @example
    #   image = Image.debian_slim("3.12").dockerfile_commands(["RUN echo 'Hello, world!'"])
    def dockerfile_commands(dockerfile_commands, context_dir: nil, strict_context: false) # rubocop:disable Metrics/MethodLength
      context_dir = self.class.send(:validate_context_dir, context_dir, strict_context)
      root, real_root = self.class.send(:context_boundary, context_dir, strict_context)
      commands = dockerfile_commands.join("\n")

      # Extract copy sources from dockerfile commands (class-level helper)
      copy_sources = self.class.send(:extract_copy_sources, commands, context_dir || '', real_root)
      copy_sources.each do |context_path, original_path|
        archive_base_path = context_path
        archive_base_path = context_path.delete_prefix(root) unless original_path.start_with?(root)
        @context_list << Context.new(source_path: context_path, archive_path: archive_base_path)
      end

      @dockerfile += "#{commands}\n"
      self
    end

    class << self
      # Creates an Image from an existing Dockerfile
      #
      # @param path [String] The path to the Dockerfile
      # @param strict_context [Boolean] When true, a COPY source that resolves outside the
      #   Dockerfile's directory is rejected instead of read. Sources that legitimately live
      #   elsewhere belong in add_local_file or add_local_dir
      # @return [Image] The image with the Dockerfile added
      #
      # @example
      #   image = Image.from_dockerfile("Dockerfile")
      def from_dockerfile(path, strict_context: false) # rubocop:disable Metrics/AbcSize, Metrics/MethodLength
        path = Pathname.new(File.expand_path(path))
        dockerfile = path.read
        img = new(dockerfile: dockerfile)

        # Remove dockerfile filename from path
        path_prefix = path.to_s.delete_suffix(path.basename.to_s)
        real_root = strict_root(path_prefix, strict_context)

        extract_copy_sources(dockerfile, path_prefix, real_root).each do |context_path, original_path|
          archive_base_path = context_path
          archive_base_path = context_path.delete_prefix(path_prefix) unless original_path.start_with?(path_prefix)
          img.context_list << Context.new(source_path: context_path, archive_path: archive_base_path)
        end

        img
      end

      # Creates an Image from an existing base image
      #
      # @param image [String] The base image to use
      # @return [Image] The image with the base image added
      #
      # @example
      #   image = Image.base("python:3.12-slim-bookworm")
      def base(image)
        img = new
        img.instance_variable_set(:@dockerfile, "FROM #{image}\n")
        img
      end

      # Creates a Debian slim image based on the official Python Docker image
      #
      # @param python_version [String, nil] The Python version to use
      # @return [Image] The image with the Debian slim image added
      #
      # @example
      #   image = Image.debian_slim("3.12")
      def debian_slim(python_version = nil) # rubocop:disable Metrics/MethodLength
        python_version = process_python_version(python_version)
        img = new
        commands = [
          "FROM python:#{python_version}-slim-bookworm",
          'RUN apt-get update',
          'RUN apt-get install -y gcc gfortran build-essential',
          'RUN pip install --upgrade pip',
          # Set debian front-end to non-interactive to avoid users getting stuck with input prompts.
          "RUN echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections"
        ]
        img.instance_variable_set(:@dockerfile, "#{commands.join("\n")}\n")
        img
      end

      private

      # Processes the Python version
      #
      # @param python_version [String, nil] The Python version to process
      # @param allow_micro_granularity [Boolean] Whether to allow micro-level granularity
      # @return [String] The processed Python version
      def process_python_version(python_version = nil)
        python_version ||= SUPPORTED_PYTHON_SERIES.last

        unless SUPPORTED_PYTHON_SERIES.include?(python_version)
          raise Sdk::Error, "Unsupported Python version: #{python_version}"
        end

        LATEST_PYTHON_MICRO_VERSIONS.select { |v| v.start_with?(python_version) }.last
      end

      # Extracts source files from COPY commands in a Dockerfile
      #
      # @param dockerfile_content [String] The content of the Dockerfile
      # @param path_prefix [String] The path prefix to use for the sources
      # @return [Array<Array<String>>] The list of the actual file path and its corresponding COPY-command source path
      def extract_copy_sources(dockerfile_content, path_prefix = '', real_root = nil) # rubocop:disable Metrics/AbcSize, Metrics/CyclomaticComplexity, Metrics/PerceivedComplexity, Metrics/MethodLength
        sources = []
        lines = dockerfile_logical_lines(dockerfile_content)

        lines.each do |line|
          # Skip empty lines and comments
          next if line.strip.empty? || line.strip.start_with?('#')

          # Check if the line contains a COPY command (at the beginning of the line)
          next unless line.match?(/^\s*COPY\s+(?!.*--from=)/i)

          # Skip COPY instructions that use heredoc syntax (inline content, not file references)
          next if line.include?('<<')

          # Extract the sources from the COPY command
          command_parts = parse_copy_command(line)
          next unless command_parts

          # Get source paths from the parsed command parts
          command_parts['sources'].each do |source|
            ensure_source_within_context(source) unless real_root.nil?

            # Handle absolute and relative paths differently
            full_path_pattern = if Pathname.new(source).absolute?
                                  # Absolute path - use as is
                                  source
                                else
                                  # Relative path - add prefix
                                  File.join(context_root(path_prefix), source)
                                end

            # Handle glob patterns
            matching_files = Dir.glob(full_path_pattern)

            if matching_files.any?
              matching_files.each do |matching_file|
                ensure_within_build_context(real_root, matching_file)
                sources << [matching_file, source]
              end
            else
              # If no files match, include the pattern anyway
              ensure_within_build_context(real_root, full_path_pattern)
              sources << [full_path_pattern, source]
            end
          end
        end

        sources
      end

      # Validates the context directory and the strict_context combination
      #
      # @param context_dir [String, nil] The caller-supplied context directory
      # @param strict_context [Boolean] Whether the build context boundary is enforced
      # @return [String, nil] The expanded context directory
      # @raise [Sdk::Error] If the directory is missing, or strict_context was asked for without one
      def validate_context_dir(context_dir, strict_context)
        # An empty string is truthy in Ruby and expands to the working directory, which would let
        # a strict context take its boundary from wherever the process was started
        context_dir = nil if context_dir.nil? || context_dir.to_s.strip.empty?

        if context_dir
          context_dir = File.expand_path(context_dir)
          raise Sdk::Error, "Context directory #{context_dir} does not exist" unless Dir.exist?(context_dir)
        elsif strict_context
          raise Sdk::Error, 'strict_context requires context_dir so that the build context boundary is explicit'
        end

        context_dir
      end

      # The join root and the strict boundary for a set of COPY sources
      #
      # @param context_dir [String, nil] The expanded context directory
      # @param strict_context [Boolean] Whether the build context boundary is enforced
      # @return [Array<String, String, nil>] The join root and the strict boundary
      def context_boundary(context_dir, strict_context)
        [context_root(context_dir || ''), strict_root(context_dir, strict_context)]
      end

      # The resolved boundary a strict build context is enforced against, or nil when the caller
      # did not ask for one
      #
      # @param root [String, nil] The build context root
      # @param strict_context [Boolean] Whether the build context boundary is enforced
      # @return [String, nil] The resolved boundary, or nil
      def strict_root(root, strict_context)
        return nil unless strict_context

        real_path(root) || root
      end

      # The build context root that a COPY source is resolved against. An empty prefix means
      # the caller supplied no context directory, in which case `docker build .` semantics
      # apply and the working directory is the context.
      #
      # @param path_prefix [String, nil] The path prefix the sources are resolved against
      # @return [String] The build context root
      def context_root(path_prefix)
        path_prefix.nil? || path_prefix.empty? ? Dir.pwd : path_prefix
      end

      # Rejects a COPY source that names something outside the build context. This is decided on
      # the source itself, before anything is read, so that a source is accepted or rejected the
      # same way whether or not it happens to exist on disk. An absolute source, including a
      # Windows drive, UNC or root-relative path, and one whose normalised form climbs above the
      # context both name something the build context cannot address. Backslashes are read as
      # separators, since that is what they are on Windows.
      #
      # @param source [String] The COPY-command source path
      # @raise [Sdk::Error] If the source names something outside the build context
      def ensure_source_within_context(source)
        # A backslash is a separator on Windows, so fold it before deciding: otherwise
        # '..\secret.txt' reads as an ordinary filename here and as a traversal there.
        candidate = Pathname.new(source.tr('\\', '/'))
        normalized = candidate.cleanpath.to_s
        outside = candidate.absolute? ||
                  source.match?(/\A[A-Za-z]:/) ||
                  normalized == '..' ||
                  normalized.start_with?('../')
        return unless outside

        raise Sdk::Error, "forbidden path outside the build context: #{source}"
      end

      # Rejects a source that resolves outside the build context. Normalisation alone cannot see
      # this: a symlinked parent directory is traversed transparently by the archiver, so a
      # regular file reached through one is stored with its contents even though the written
      # path stays inside the context.
      #
      # @param real_root [String] The resolved build context root
      # @param candidate [String] The resolved source path to check
      # @raise [Sdk::Error] If the candidate resolves outside the build context
      def ensure_within_build_context(real_root, candidate)
        # real_root is nil unless the caller asked for a strict build context
        return if real_root.nil?

        resolved = real_path(candidate)
        # A path that does not exist cannot be archived. A symlink whose target is missing still
        # leaves the context, so it is rejected rather than tolerated.
        return if resolved.nil? && !File.symlink?(candidate)

        raise Sdk::Error, "forbidden path outside the build context: #{candidate}" if resolved.nil?
        raise Sdk::Error, "forbidden path outside the build context: #{resolved}" unless within?(real_root, resolved)
      end

      # Whether a resolved path lies inside the resolved build context root
      #
      # @param real_root [String] The resolved build context root
      # @param resolved [String] The resolved candidate path
      # @return [Boolean]
      def within?(real_root, resolved)
        relative = Pathname.new(resolved).relative_path_from(Pathname.new(real_root)).to_s
        relative != '..' && !relative.start_with?("..#{File::SEPARATOR}")
      rescue ArgumentError
        false
      end

      # Resolves a path to its real location, or nil when it cannot be resolved
      #
      # @param path [String] The path to resolve
      # @return [String, nil] The real path, or nil
      def real_path(path)
        File.realpath(path)
      rescue SystemCallError
        nil
      end

      # Joins backslash-continued physical lines into logical Dockerfile instruction lines
      #
      # @param dockerfile_content [String] The content of the Dockerfile
      # @return [Array<String>] The logical instruction lines
      def dockerfile_logical_lines(dockerfile_content) # rubocop:disable Metrics/CyclomaticComplexity, Metrics/PerceivedComplexity, Metrics/MethodLength
        logical_lines = []
        current = nil

        dockerfile_content.each_line(chomp: true) do |physical_line|
          is_comment = physical_line.lstrip.start_with?('#')
          # Docker drops empty and comment lines that appear inside a continued instruction
          next if current && (physical_line.strip.empty? || is_comment)

          stripped = physical_line.rstrip
          # A trailing backslash on a comment line is literal; comments never continue onto the next line
          continued = !is_comment && stripped.end_with?('\\')
          segment = continued ? stripped[0..-2] : physical_line
          current = current ? current + segment : segment
          next if continued

          logical_lines << current
          current = nil
        end

        logical_lines << current if current
        logical_lines
      end

      # Parses a COPY command to extract sources and destination
      #
      # @param line [String] The line to parse
      # @return [Hash, nil] A hash containing the sources and destination, or nil if parsing fails
      def parse_copy_command(line)
        # Remove initial "COPY" and strip whitespace
        parts = line.strip[4..].strip

        # Skip leading flags. Value-taking flags use the --flag=value form (--chown=..., --chmod=...)
        # and boolean flags stand alone (--link), so a flag never consumes the token that follows it.
        parts = parts.sub(/\A\S+\s*/, '') while parts.start_with?('--')

        # Handle JSON array format: COPY ["src1", "src2", "dest"]
        return parse_json_copy_command(parts) if parts.start_with?('[')

        # Handle the whitespace-separated format
        elements = Shellwords.split(parts)
        return nil if elements.length < 2

        { 'sources' => elements[0..-2], 'dest' => elements[-1] }
      rescue ArgumentError
        nil
      end

      def parse_json_copy_command(parts)
        elements = JSON.parse(parts)
        return nil unless elements.is_a?(Array) && elements.all?(String) && elements.length >= 2

        { 'sources' => elements[0..-2], 'dest' => elements[-1] }
      rescue JSON::ParserError
        nil
      end
    end

    private

    # Flattens a list of strings and arrays of strings into a single array of strings
    #
    # @param function_name [String] The name of the function that is being called
    # @param arg_name [String] The name of the argument that is being passed
    # @param args [Array] The list of arguments to flatten
    # @return [Array<String>] A list of strings
    def flatten_str_args(function_name, arg_name, args) # rubocop:disable Metrics/MethodLength
      ret = []
      args.each do |x|
        case x
        when String
          ret << x
        when Array
          unless x.all? { |y| y.is_a?(String) }
            raise Sdk::Error, "#{function_name}: #{arg_name} must only contain strings"
          end

          ret.concat(x)

        else
          raise Sdk::Error, "#{function_name}: #{arg_name} must only contain strings"
        end
      end
      ret
    end

    # Formats the arguments in a single string
    #
    # @param find_links [Array<String>, nil] The find-links to use
    # @param index_url [String, nil] The index URL to use
    # @param extra_index_urls [Array<String>, nil] The extra index URLs to use
    # @param pre [Boolean] Whether to install pre-release packages
    # @param extra_options [String] Additional options to pass to pip
    # @return [String] The formatted arguments
    def format_pip_install_args(find_links: nil, index_url: nil, extra_index_urls: nil, pre: false, extra_options: '') # rubocop:disable Metrics/CyclomaticComplexity, Metrics/PerceivedComplexity
      extra_args = ''
      find_links&.each { |find_link| extra_args += " --find-links #{Shellwords.escape(find_link)}" }
      extra_args += " --index-url #{Shellwords.escape(index_url)}" if index_url
      extra_index_urls&.each do |extra_index_url|
        extra_args += " --extra-index-url #{Shellwords.escape(extra_index_url)}"
      end
      extra_args += ' --pre' if pre
      extra_args += " #{extra_options.strip}" if extra_options && !extra_options.strip.empty?

      extra_args
    end
  end
end
