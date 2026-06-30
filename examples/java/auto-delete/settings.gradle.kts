rootProject.name = "auto-delete"

dependencyResolutionManagement {
    repositories {
        mavenLocal()
        mavenCentral()
    }
}

includeBuild("../../../sdk-java") {
    dependencySubstitution {
        substitute(module("io.daytona:sdk-java")).using(project(":"))
    }
}
