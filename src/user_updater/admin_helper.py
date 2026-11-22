"""Narrow polkit entry point for administrator settings and immediate updates."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .policy import PolicyError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="user-updater-admin")
    parser.add_argument(
        "action",
        choices=("authorize-settings", "write-policy", "run-now", "run-user-tools-now"),
    )
    args = parser.parse_args(argv)

    try:
        _require_trusted_root_install()
        if args.action == "authorize-settings":
            return 0

        from . import cli

        if args.action == "write-policy":
            return cli._write_admin_policy()
        if args.action == "run-now":
            return cli._run_system_now(system_mode=True)
        return cli._run_user_tools_now(system_mode=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if isinstance(exc.stderr, str) else ""
        print(
            f"user-updater-admin: command failed with status {exc.returncode}"
            + (f": {detail}" if detail else ""),
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, ValueError, PolicyError) as exc:
        print(f"user-updater-admin: {exc}", file=sys.stderr)
        return 1


def _require_trusted_root_install() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("administrator actions must be launched through polkit")

    for installed_file in (Path(sys.argv[0]).resolve(strict=True), Path(__file__).resolve()):
        for path in (installed_file, *installed_file.parents):
            metadata = path.stat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise RuntimeError(
                    "administrator actions require a root-owned, non-writable installation"
                )


if __name__ == "__main__":
    raise SystemExit(main())
