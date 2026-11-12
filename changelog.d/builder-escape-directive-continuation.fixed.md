**`ImageBuilder` refuses a raw line ending in the base's escape character** —
under a `from_dockerfile` / `fromDockerfile` base that selects `# escape=`, a
`RUN`, `WORKDIR` or `USER` value ending in that escape character raises
`BadRequestError`, as it would continue onto the next instruction.
