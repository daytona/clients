# Build and install the Ruby SDK gems from the checked-out source. Debian's Ruby is
# 3.1 and the SDK needs >= 3.2, so use a prebuilt ruby-builder binary (the same
# builds ruby/setup-ruby uses; they expect the hostedtoolcache prefix).
t "install ruby 3.3"
apt-get update -qq
apt-get install -y -qq libyaml-0-2 build-essential libffi-dev >/dev/null
mkdir -p /opt/hostedtoolcache/Ruby/3.3.6
curl -fsSL --retry 3 https://github.com/ruby/ruby-builder/releases/download/ruby-3.3.6/ruby-3.3.6-ubuntu-22.04-x64.tar.gz |
  tar -C /opt/hostedtoolcache/Ruby/3.3.6 -xz
ln -sf /opt/hostedtoolcache/Ruby/3.3.6/x64/bin/* /usr/local/bin/
ruby --version

cd /opt/clients
GEM_DIRS="api-client-ruby toolbox-api-client-ruby analytics-api-client-ruby sdk-ruby"
# Prerelease versions (0.0.0.pre.dev) are skipped by a plain `require`, so stamp a
# release version the same way the release pipeline does.
# shellcheck disable=SC2086
{ grep -rl "0.0.0.pre.dev" $GEM_DIRS --include=*.rb --include=*.gemspec || true; } | xargs -r sed -i 's/0\.0\.0\.pre\.dev/0.0.1/g'
t "build gems"
for d in $GEM_DIRS; do (cd "$d" && rm -f ./*.gem && gem build ./*.gemspec | grep -E 'File:'); done
t "install third-party dependencies"
cat >/tmp/deps.rb <<'RB'
require "rubygems/package"
require "rubygems/dependency_installer"
deps = Dir["{api-client-ruby,toolbox-api-client-ruby,analytics-api-client-ruby,sdk-ruby}/*.gem"]
  .flat_map { |f| Gem::Package.new(f).spec.runtime_dependencies }
  .reject { |d| d.name.start_with?("daytona") }
  .uniq(&:name)
deps.each { |d| puts "dep #{d}"; Gem::DependencyInstaller.new(document: []).install(d.name, d.requirement) }
RB
retry ruby /tmp/deps.rb >/tmp/gem-deps.log 2>&1 || { tail -n 25 /tmp/gem-deps.log; exit 1; }
t "install daytona gems"
gem install --local --ignore-dependencies --no-document api-client-ruby/*.gem toolbox-api-client-ruby/*.gem \
  analytics-api-client-ruby/*.gem sdk-ruby/*.gem
ruby -e 'require "daytona"; puts "daytona #{Gem.loaded_specs["daytona"].version}"'
t done
