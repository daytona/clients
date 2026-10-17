# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The v1 namespace: the SDK 1.x API in the shape SDK 2.x keeps.

`Daytona` / `AsyncDaytona` here are the v1 clients (not the deprecated root wrappers); every other
name is the same object as at the package root. `...V1` aliases name the types SDK 2.x also exports
from `daytona.v2`, so both can be imported side by side.
"""

# pylint: disable=useless-import-alias

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

_ALIASES: dict[str, str] = {
    "AsyncDaytonaV1": "AsyncDaytona",
    "AsyncSandboxV1": "AsyncSandbox",
    "DaytonaV1": "Daytona",
    "DaytonaAuthenticationErrorV1": "DaytonaAuthenticationError",
    "DaytonaAuthorizationErrorV1": "DaytonaAuthorizationError",
    "DaytonaConfigV1": "DaytonaConfig",
    "DaytonaConflictErrorV1": "DaytonaConflictError",
    "DaytonaConnectionErrorV1": "DaytonaConnectionError",
    "DaytonaErrorV1": "DaytonaError",
    "DaytonaNotFoundErrorV1": "DaytonaNotFoundError",
    "DaytonaRateLimitErrorV1": "DaytonaRateLimitError",
    "DaytonaTimeoutErrorV1": "DaytonaTimeoutError",
    "DaytonaValidationErrorV1": "DaytonaValidationError",
    "ImageV1": "Image",
    "ListSandboxesQueryV1": "ListSandboxesQuery",
    "ResourcesV1": "Resources",
    "SandboxV1": "Sandbox",
    "VolumeMountV1": "VolumeMount",
}

# Explicit `X as X` re-exports so installed type checkers (mypy, pyright) resolve every name here,
# in particular `Daytona` / `AsyncDaytona` to the v1 clients rather than the root wrappers.
if TYPE_CHECKING:
    from daytona_api_client import SandboxListSortDirection as SandboxListSortDirection
    from daytona_api_client import SandboxListSortField as SandboxListSortField
    from daytona_api_client import SandboxState as SandboxState
    from daytona_api_client_async import GpuType as GpuType
    from daytona_api_client_async import SandboxClass as SandboxClass
    from daytona_toolbox_api_client import SessionExecuteResponse as SessionExecuteResponse

    from ._async.computer_use import AsyncComputerUse as AsyncComputerUse
    from ._async.computer_use import AsyncDisplay as AsyncDisplay
    from ._async.computer_use import AsyncKeyboard as AsyncKeyboard
    from ._async.computer_use import AsyncMouse as AsyncMouse
    from ._async.computer_use import AsyncScreenshot as AsyncScreenshot
    from ._async.daytona import AsyncDaytona as AsyncDaytona
    from ._async.daytona import AsyncDaytona as AsyncDaytonaV1
    from ._async.sandbox import AsyncSandbox as AsyncSandbox
    from ._async.sandbox import AsyncSandbox as AsyncSandboxV1
    from ._sync.daytona import Daytona as Daytona
    from ._sync.daytona import Daytona as DaytonaV1
    from ._sync.sandbox import Sandbox as Sandbox
    from ._sync.sandbox import Sandbox as SandboxV1
    from .common.charts import BarChart as BarChart
    from .common.charts import BoxAndWhiskerChart as BoxAndWhiskerChart
    from .common.charts import Chart as Chart
    from .common.charts import ChartType as ChartType
    from .common.charts import CompositeChart as CompositeChart
    from .common.charts import LineChart as LineChart
    from .common.charts import PieChart as PieChart
    from .common.charts import ScatterChart as ScatterChart
    from .common.code_interpreter import ExecutionError as ExecutionError
    from .common.code_interpreter import ExecutionResult as ExecutionResult
    from .common.code_interpreter import OutputMessage as OutputMessage
    from .common.computer_use import ScreenshotOptions as ScreenshotOptions
    from .common.computer_use import ScreenshotRegion as ScreenshotRegion
    from .common.daytona import V1_API_URL as V1_API_URL
    from .common.daytona import CodeLanguage as CodeLanguage
    from .common.daytona import CreateSandboxBaseParams as CreateSandboxBaseParams
    from .common.daytona import CreateSandboxFromImageParams as CreateSandboxFromImageParams
    from .common.daytona import CreateSandboxFromSnapshotParams as CreateSandboxFromSnapshotParams
    from .common.daytona import DaytonaConfig as DaytonaConfig
    from .common.daytona import DaytonaConfig as DaytonaConfigV1
    from .common.errors import DaytonaA11yUnavailableError as DaytonaA11yUnavailableError
    from .common.errors import DaytonaAuthenticationError as DaytonaAuthenticationError
    from .common.errors import DaytonaAuthenticationError as DaytonaAuthenticationErrorV1
    from .common.errors import DaytonaAuthorizationError as DaytonaAuthorizationError
    from .common.errors import DaytonaAuthorizationError as DaytonaAuthorizationErrorV1
    from .common.errors import DaytonaBadGatewayError as DaytonaBadGatewayError
    from .common.errors import DaytonaBadRequestError as DaytonaBadRequestError
    from .common.errors import DaytonaCommandAlreadyCompletedError as DaytonaCommandAlreadyCompletedError
    from .common.errors import DaytonaConflictError as DaytonaConflictError
    from .common.errors import DaytonaConflictError as DaytonaConflictErrorV1
    from .common.errors import DaytonaConnectionError as DaytonaConnectionError
    from .common.errors import DaytonaConnectionError as DaytonaConnectionErrorV1
    from .common.errors import DaytonaConnectionTimeoutError as DaytonaConnectionTimeoutError
    from .common.errors import DaytonaError as DaytonaError
    from .common.errors import DaytonaError as DaytonaErrorV1
    from .common.errors import DaytonaFileAccessDeniedError as DaytonaFileAccessDeniedError
    from .common.errors import DaytonaFileNotFoundError as DaytonaFileNotFoundError
    from .common.errors import DaytonaFileReadFailedError as DaytonaFileReadFailedError
    from .common.errors import DaytonaForbiddenError as DaytonaForbiddenError
    from .common.errors import DaytonaGitAuthFailedError as DaytonaGitAuthFailedError
    from .common.errors import DaytonaGitBranchExistsError as DaytonaGitBranchExistsError
    from .common.errors import DaytonaGitBranchNotFoundError as DaytonaGitBranchNotFoundError
    from .common.errors import DaytonaGitDirtyWorktreeError as DaytonaGitDirtyWorktreeError
    from .common.errors import DaytonaGitMergeConflictError as DaytonaGitMergeConflictError
    from .common.errors import DaytonaGitPushRejectedError as DaytonaGitPushRejectedError
    from .common.errors import DaytonaGitRemoteRejectedError as DaytonaGitRemoteRejectedError
    from .common.errors import DaytonaGitRepoNotFoundError as DaytonaGitRepoNotFoundError
    from .common.errors import DaytonaGitTransportFailedError as DaytonaGitTransportFailedError
    from .common.errors import DaytonaGoneError as DaytonaGoneError
    from .common.errors import DaytonaInternalServerError as DaytonaInternalServerError
    from .common.errors import DaytonaInvalidFilePathError as DaytonaInvalidFilePathError
    from .common.errors import DaytonaLspServerNotInitializedError as DaytonaLspServerNotInitializedError
    from .common.errors import DaytonaNotFoundError as DaytonaNotFoundError
    from .common.errors import DaytonaNotFoundError as DaytonaNotFoundErrorV1
    from .common.errors import DaytonaProcessExecutionTimeoutError as DaytonaProcessExecutionTimeoutError
    from .common.errors import DaytonaProcessNotFoundError as DaytonaProcessNotFoundError
    from .common.errors import DaytonaQueueTimeoutError as DaytonaQueueTimeoutError
    from .common.errors import DaytonaRateLimitError as DaytonaRateLimitError
    from .common.errors import DaytonaRateLimitError as DaytonaRateLimitErrorV1
    from .common.errors import DaytonaRecordingFfmpegNotFoundError as DaytonaRecordingFfmpegNotFoundError
    from .common.errors import DaytonaRecordingStillActiveError as DaytonaRecordingStillActiveError
    from .common.errors import DaytonaServiceUnavailableError as DaytonaServiceUnavailableError
    from .common.errors import DaytonaSessionEndedError as DaytonaSessionEndedError
    from .common.errors import DaytonaSpotEvictedError as DaytonaSpotEvictedError
    from .common.errors import DaytonaTimeoutError as DaytonaTimeoutError
    from .common.errors import DaytonaTimeoutError as DaytonaTimeoutErrorV1
    from .common.errors import DaytonaUnprocessableEntityError as DaytonaUnprocessableEntityError
    from .common.errors import DaytonaValidationError as DaytonaValidationError
    from .common.errors import DaytonaValidationError as DaytonaValidationErrorV1
    from .common.filesystem import CancelEvent as CancelEvent
    from .common.filesystem import DownloadProgress as DownloadProgress
    from .common.filesystem import FileDownloadErrorDetails as FileDownloadErrorDetails
    from .common.filesystem import FileDownloadRequest as FileDownloadRequest
    from .common.filesystem import FileDownloadResponse as FileDownloadResponse
    from .common.filesystem import FileUpload as FileUpload
    from .common.filesystem import UploadProgress as UploadProgress
    from .common.image import Image as Image
    from .common.image import Image as ImageV1
    from .common.lsp_server import LspCompletionPosition as LspCompletionPosition
    from .common.lsp_server import LspLanguageId as LspLanguageId
    from .common.process import CodeRunParams as CodeRunParams
    from .common.process import ExecuteResponse as ExecuteResponse
    from .common.process import ExecutionArtifacts as ExecutionArtifacts
    from .common.process import OutputHandler as OutputHandler
    from .common.process import SessionExecuteRequest as SessionExecuteRequest
    from .common.pty import PtySize as PtySize
    from .common.sandbox import ListSandboxesQuery as ListSandboxesQuery
    from .common.sandbox import ListSandboxesQuery as ListSandboxesQueryV1
    from .common.sandbox import Resources as Resources
    from .common.sandbox import Resources as ResourcesV1
    from .common.sandbox import SandboxMetrics as SandboxMetrics
    from .common.secret import CreateSecretParams as CreateSecretParams
    from .common.secret import ListSecretsResponse as ListSecretsResponse
    from .common.secret import Secret as Secret
    from .common.secret import UpdateSecretParams as UpdateSecretParams
    from .common.snapshot import CreateSnapshotParams as CreateSnapshotParams
    from .common.volume import VolumeMount as VolumeMount
    from .common.volume import VolumeMount as VolumeMountV1
    from .common.warm_pool import WarmPool as WarmPool

__all__ = [
    "Daytona",
    "DaytonaConfig",
    "V1_API_URL",
    "CodeLanguage",
    "SessionExecuteRequest",
    "SessionExecuteResponse",
    "DaytonaError",
    "DaytonaNotFoundError",
    "DaytonaRateLimitError",
    "LspLanguageId",
    "CodeRunParams",
    "Sandbox",
    "Resources",
    "SandboxMetrics",
    "GpuType",
    "SandboxClass",
    "SandboxState",
    "SandboxListSortField",
    "SandboxListSortDirection",
    "ChartType",
    "Chart",
    "LineChart",
    "ScatterChart",
    "BarChart",
    "PieChart",
    "BoxAndWhiskerChart",
    "CompositeChart",
    "FileDownloadRequest",
    "FileDownloadResponse",
    "FileDownloadErrorDetails",
    "DownloadProgress",
    "UploadProgress",
    "CancelEvent",
    "FileUpload",
    "VolumeMount",
    "WarmPool",
    "Secret",
    "CreateSecretParams",
    "UpdateSecretParams",
    "ListSecretsResponse",
    "ListSandboxesQuery",
    "AsyncDaytona",
    "AsyncSandbox",
    "AsyncComputerUse",
    "AsyncMouse",
    "AsyncKeyboard",
    "AsyncScreenshot",
    "AsyncDisplay",
    "ScreenshotRegion",
    "ScreenshotOptions",
    "Image",
    "CreateSandboxBaseParams",
    "CreateSandboxFromImageParams",
    "CreateSandboxFromSnapshotParams",
    "CreateSnapshotParams",
    "PtySize",
    "LspCompletionPosition",
    "ExecutionArtifacts",
    "ExecuteResponse",
    "ExecutionResult",
    "ExecutionError",
    "OutputMessage",
    "OutputHandler",
    "DaytonaTimeoutError",
    "DaytonaAuthenticationError",
    "DaytonaAuthorizationError",
    "DaytonaBadRequestError",
    "DaytonaConflictError",
    "DaytonaForbiddenError",
    "DaytonaValidationError",
    "DaytonaConnectionError",
    "DaytonaConnectionTimeoutError",
    "DaytonaGoneError",
    "DaytonaUnprocessableEntityError",
    "DaytonaInternalServerError",
    "DaytonaBadGatewayError",
    "DaytonaServiceUnavailableError",
    "DaytonaGitAuthFailedError",
    "DaytonaGitRepoNotFoundError",
    "DaytonaGitBranchNotFoundError",
    "DaytonaGitBranchExistsError",
    "DaytonaGitPushRejectedError",
    "DaytonaGitDirtyWorktreeError",
    "DaytonaGitMergeConflictError",
    "DaytonaGitTransportFailedError",
    "DaytonaGitRemoteRejectedError",
    "DaytonaFileNotFoundError",
    "DaytonaFileAccessDeniedError",
    "DaytonaInvalidFilePathError",
    "DaytonaFileReadFailedError",
    "DaytonaLspServerNotInitializedError",
    "DaytonaProcessExecutionTimeoutError",
    "DaytonaProcessNotFoundError",
    "DaytonaQueueTimeoutError",
    "DaytonaSessionEndedError",
    "DaytonaCommandAlreadyCompletedError",
    "DaytonaA11yUnavailableError",
    "DaytonaRecordingStillActiveError",
    "DaytonaRecordingFfmpegNotFoundError",
    "DaytonaSpotEvictedError",
    "AsyncDaytonaV1",
    "AsyncSandboxV1",
    "DaytonaV1",
    "DaytonaAuthenticationErrorV1",
    "DaytonaAuthorizationErrorV1",
    "DaytonaConfigV1",
    "DaytonaConflictErrorV1",
    "DaytonaConnectionErrorV1",
    "DaytonaErrorV1",
    "DaytonaNotFoundErrorV1",
    "DaytonaRateLimitErrorV1",
    "DaytonaTimeoutErrorV1",
    "DaytonaValidationErrorV1",
    "ImageV1",
    "ListSandboxesQueryV1",
    "ResourcesV1",
    "SandboxV1",
    "VolumeMountV1",
]

_root = importlib.import_module(__name__.rpartition(".")[0])


def __getattr__(attr_name: str) -> object:
    if attr_name == "Daytona":
        value = getattr(importlib.import_module("._sync.daytona", __package__), attr_name)
        globals()[attr_name] = value
        return value

    if attr_name == "AsyncDaytona":
        value = getattr(importlib.import_module("._async.daytona", __package__), attr_name)
        globals()[attr_name] = value
        return value

    canonical_name = _ALIASES.get(attr_name)
    if canonical_name is not None:
        value = getattr(importlib.import_module(__name__), canonical_name)
        globals()[attr_name] = value
        return value

    if attr_name in __all__:
        value = getattr(_root, attr_name)
        globals()[attr_name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {attr_name!r}")


def __dir__() -> list[str]:
    return list(__all__)
