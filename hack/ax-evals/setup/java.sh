# Publish the Java SDK and its generated clients from the checked-out source to the
# local Maven repository as 0.0.0-ci.
command -v javac >/dev/null && javac -version 2>&1 | grep -Eq 'javac (1[7-9]|[2-9][0-9])' || {
  t "install jdk"
  apt-get update -qq
  apt-get install -y -qq openjdk-17-jdk-headless >/dev/null
}
java -version 2>&1 | head -n 1
# The sandbox locale is POSIX; the sources contain UTF-8 (e.g. em dashes in comments).
export LC_ALL=C.UTF-8 JAVA_TOOL_OPTIONS=-Dfile.encoding=UTF-8
cd /opt/clients
sed -i -E "s/^version = .*/version = '0.0.0-ci'/" api-client-java/build.gradle toolbox-api-client-java/build.gradle \
  analytics-api-client-java/build.gradle
sed -i -E 's/^version = .*/version = "0.0.0-ci"/' sdk-java/build.gradle.kts
for d in api-client-java toolbox-api-client-java analytics-api-client-java sdk-java; do
  t "publishToMavenLocal $d"
  (cd "$d" && chmod +x gradlew && retry ./gradlew -q --no-daemon publishToMavenLocal -x test -x javadoc)
done
find ~/.m2/repository/io/daytona -name '*0.0.0-ci.jar'

# A ready Gradle project with every dependency already resolved, so the eval measures
# the SDK rather than the agent's Gradle setup and dependency downloads.
t "prepare /app/javaeval"
mkdir -p /app/javaeval/src/main/java/eval
cp -r /opt/clients/sdk-java/gradle /opt/clients/sdk-java/gradlew /app/javaeval/
cd /app/javaeval
cat >settings.gradle.kts <<'KTS'
rootProject.name = "javaeval"
KTS
cat >build.gradle.kts <<'KTS'
plugins {
    application
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

repositories {
    mavenLocal()
    mavenCentral()
}

dependencies {
    implementation("io.daytona:sdk:0.0.0-ci")
}

application {
    mainClass.set("eval.Main")
}
KTS
cat >src/main/java/eval/Main.java <<'JAVA'
package eval;

public class Main {
    public static void main(String[] args) {
        System.out.println("replace me");
    }
}
JAVA
retry ./gradlew -q --no-daemon build
./gradlew -q --no-daemon dependencies --configuration runtimeClasspath | grep 'io.daytona'
t done
