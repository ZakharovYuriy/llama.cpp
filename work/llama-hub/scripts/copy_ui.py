#!/usr/bin/env python3
import shutil
import sys
from pathlib import Path


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    src_dir = Path("/workspace/tools/server/public")
    dst_dir = root_dir / "static"

    if not src_dir.is_dir():
        print(f"UI source directory not found: {src_dir}", file=sys.stderr)
        print(
            "Build the web UI first: cd /workspace/tools/server/webui && npm run build",
            file=sys.stderr,
        )
        return 1

    dst_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(src_dir / "index.html.gz", dst_dir / "index.html.gz")
    shutil.copy2(src_dir / "loading.html", dst_dir / "loading.html")

    print(f"Copied UI assets into {dst_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
