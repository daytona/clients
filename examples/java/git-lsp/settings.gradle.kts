rootProject.name = "git-lsp"

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
