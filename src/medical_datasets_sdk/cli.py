from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path

from .client import MedicalDatasetsClient, MedicalDatasetsError


DEFAULT_URL = "http://10.20.13.1:24174"


def progress(label: str, done: int, total: int) -> None:
    percent = 100 if total == 0 else min(100, int(done * 100 / total))
    print(f"\r{percent:3d}% {label} {done}/{total}", end="", file=sys.stderr, flush=True)
    if done >= total:
        print(file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="medical-datasets", description="Medical Datasets SDK command line client")
    root.add_argument("--base-url", default=os.getenv("MEDICAL_DATASETS_URL", DEFAULT_URL), help="platform URL")
    root.add_argument("--version", action="version", version=version("medical-datasets-sdk"))
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("auth-check", help="validate the configured SDK Key")

    upload = commands.add_parser("upload", help="upload a file, archive, or directory")
    upload.add_argument("source", type=Path)
    target = upload.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="create a new dataset with this name")
    target.add_argument("--dataset-slug", help="append to an existing dataset")
    upload.add_argument("--source-name", default="SDK upload")
    upload.add_argument("--summary", default="")
    upload.add_argument("--category", default="Other")
    upload.add_argument("--message", default="SDK upload")
    upload.add_argument("--keep-archives", action="store_true")

    download = commands.add_parser("download", help="download a complete dataset")
    download.add_argument("slug")
    download.add_argument("--destination", type=Path, default=Path("."))
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    client = MedicalDatasetsClient(args.base_url)
    try:
        if args.command == "auth-check":
            user = client.whoami()
            print(json.dumps(user, ensure_ascii=False))
            return 0
        if args.command == "upload":
            result = client.upload_dataset(
                args.source,
                dataset_slug=args.dataset_slug,
                name=args.name,
                source_name=args.source_name,
                summary=args.summary,
                category=args.category,
                message=args.message,
                keep_archives=args.keep_archives,
                progress=progress,
            )
            print(json.dumps({"dataset": result["dataset"], "revision": result["revision"]}, ensure_ascii=False))
            return 0
        if args.command == "download":
            destination = client.download_dataset(args.slug, args.destination, progress=progress)
            print(destination)
            return 0
    except (MedicalDatasetsError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
