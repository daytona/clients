# Publish the Java SDK and its generated clients from the checked-out source to the
# local Maven repository as 0.0.0-ci.
command -v java >/dev/null || {
  t "install jdk"
  apt-get update -qq
  apt-get install -y -qq openjdk-17-jdk-headless >/dev/null
}
java -version 2>&1 | head -n 1
cd /opt/clients
sed -i -E "s/^version = .*/version = '0.0.0-ci'/" api-client-java/build.gradle toolbox-api-client-java/build.gradle \
  analytics-api-client-java/build.gradle
sed -i -E 's/^version = .*/version = "0.0.0-ci"/' sdk-java/build.gradle.kts
for d in api-client-java toolbox-api-client-java analytics-api-client-java sdk-java; do
  t "publishToMavenLocal $d"
  (cd "$d" && chmod +x gradlew && retry ./gradlew -q --no-daemon publishToMavenLocal -x test -x javadoc)
done
find ~/.m2/repository/io/daytona -name '*0.0.0-ci.jar'
t done
