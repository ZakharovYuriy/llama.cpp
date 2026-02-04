from pathlib import Path
from typing import Optional

from aiohttp import web


class UIAssets:
    def __init__(self, static_dir: Path):
        self.static_dir = static_dir
        self.index_gz = (static_dir / "index.html.gz").read_bytes()
        self.loading_html = (static_dir / "loading.html").read_bytes()
        self.setup_html = (static_dir / "setup.html").read_bytes()

    def index_response(self) -> web.Response:
        return web.Response(
            body=self.index_gz,
            content_type="text/html",
            charset="utf-8",
            headers={
                "Content-Encoding": "gzip",
                "Cross-Origin-Embedder-Policy": "require-corp",
                "Cross-Origin-Opener-Policy": "same-origin",
            },
        )

    def loading_response(self, status: int = 503) -> web.Response:
        return web.Response(
            body=self.loading_html,
            status=status,
            content_type="text/html",
            charset="utf-8",
        )

    def setup_response(self) -> web.Response:
        return web.Response(
            body=self.setup_html,
            content_type="text/html",
            charset="utf-8",
        )


def resolve_static_dir(cli_path: Optional[str]) -> Path:
    if cli_path:
        return Path(cli_path).resolve()
    return Path(__file__).resolve().parent.parent / "static"
