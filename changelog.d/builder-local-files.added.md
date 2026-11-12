**Local files in `ImageBuilder`** — `add_local_file` / `addLocalFile`,
`add_local_dir` / `addLocalDir` and `pip_install_from_requirements` /
`pipInstallFromRequirements` copy local files into the image: `images.build()`
and `create_from_build()` / `createFromBuild()` pack the rendered Dockerfile
and the files into a build context and upload it, and an added directory's
`.dockerignore` is honoured. In TypeScript they need Node.js.
