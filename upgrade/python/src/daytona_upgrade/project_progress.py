# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Throttled progress callbacks for large project preflights."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Timer


@contextmanager
def slow_progress(report: Callable[[str], None] | None) -> Iterator[None]:
    """Give even a single slow source a status line; cancel and join on exit."""
    if report is None:
        yield
        return
    timer = Timer(
        3,
        report,
        args=("Still analyzing Python sources (more than 3 seconds elapsed)",),
    )
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join()


@dataclass(frozen=True)
class ProjectProgress:
    """Keep progress cadence separate from migration planning."""

    total: int
    report: Callable[[str], None]

    def __call__(self, message: str) -> None:
        self.report(message)

    def advance(self, number: int, file: str) -> None:
        if number == 1 or number % 50 == 0 or number == self.total:
            self.report(f"Analyzing {number}/{self.total}: {file}")


def project_progress(
    total: int, report: Callable[[str], None] | None
) -> ProjectProgress | None:
    """Enable progress only above the existing 200-file threshold."""
    if total <= 200 or report is None:
        return None
    progress = ProjectProgress(total, report)
    progress(f"Discovered {total} files; indexing Python imports")
    return progress
