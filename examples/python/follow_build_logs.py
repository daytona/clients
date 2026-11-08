"""Follow an existing image or sandbox build without retaining its transcript.

Run: python python/follow_build_logs.py image IMAGE_ID
  or python python/follow_build_logs.py sandbox SANDBOX_ID
Uses DAYTONA_API_KEY (and DAYTONA_V2_API_URL in test environments). Requires live build-log support.
"""
from __future__ import annotations

import argparse
import sys

from daytona import Daytona
from daytona.v2 import BuildLogEvent, BuildLogInterruptedError


def on_logs(chunk: str) -> None:
    sys.stdout.write(chunk)
    sys.stdout.flush()


def on_event(event: BuildLogEvent) -> None:
    if event.type in ("state", "gap", "unavailable", "source_changed", "error"):
        print(event, file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("image", "sandbox"))
    parser.add_argument("id")
    parser.add_argument("--cursor")
    args = parser.parse_args()
    client = Daytona().v2
    try:
        if args.kind == "image":
            result = client.images.follow_build_logs(args.id, on_logs=on_logs, on_event=on_event, cursor=args.cursor)
        else:
            result = client.get(args.id).follow_build_logs(on_logs=on_logs, on_event=on_event, cursor=args.cursor)
        print(result, file=sys.stderr)
    except BuildLogInterruptedError as error:
        print(f"{error.code}: {error}; last checkpoint: {error.cursor}", file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        client.close()


if __name__ == "__main__":
    main()
