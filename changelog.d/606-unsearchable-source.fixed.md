**A local source made unsearchable after it was added raises a typed error** —
when a parent directory of an `add_local_file`, `add_local_dir` or
`pip_install_from_requirements` (`addLocalFile`, `addLocalDir`,
`pipInstallFromRequirements`) source becomes unsearchable before the build,
the build now raises `BadRequestError` (`build context file is not
readable`) instead of a raw permission error.
