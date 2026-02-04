import logging
from typing import Dict, Optional

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

from .ui import UIAssets

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def normalize_prefix(prefix: Optional[str]) -> str:
    if not prefix or prefix == "/":
        return ""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if prefix.endswith("/"):
        prefix = prefix[:-1]
    return prefix


def filter_headers(headers) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in HOP_HEADERS:
            continue
        out[key] = value
    return out


class HubProxy:
    def __init__(
        self,
        backend_base: Optional[str],
        api_prefix: str,
        ui_assets: UIAssets,
        health_timeout: float = 0.5,
        ssl_verify: bool = True,
        redirect_root: bool = True,
    ) -> None:
        self.backend_base = backend_base.rstrip("/") if backend_base else None
        self.api_prefix = normalize_prefix(api_prefix)
        self.ui_assets = ui_assets
        self.health_timeout = health_timeout
        self.redirect_root = redirect_root
        self._backend_ready = False
        connector = TCPConnector(ssl=ssl_verify)
        self.session = ClientSession(timeout=ClientTimeout(total=None), connector=connector)

    def ui_prefix(self) -> str:
        return self.api_prefix

    def set_backend(self, backend_base: str, api_prefix: str) -> None:
        self.backend_base = backend_base.rstrip("/")
        self.api_prefix = normalize_prefix(api_prefix)
        self._backend_ready = False

    def _ui_paths(self) -> Dict[str, str]:
        base = self.ui_prefix()
        if base:
            return {
                base: "redirect",
                base + "/": "index",
                base + "/index.html": "index",
                base + "/loading.html": "loading",
            }
        return {
            "/": "index",
            "/index.html": "index",
            "/loading.html": "loading",
        }

    def _setup_paths(self) -> Dict[str, str]:
        return {
            "/": "setup",
            "/setup": "setup",
            "/setup/": "setup",
        }

    def _is_proxy_path(self, path: str) -> bool:
        if not self.api_prefix:
            return True
        return path == self.api_prefix or path.startswith(self.api_prefix + "/")

    def _cors_headers(self, request: web.Request) -> Dict[str, str]:
        origin = request.headers.get("Origin")
        headers: Dict[str, str] = {}
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
            headers["Access-Control-Allow-Credentials"] = "true"
        return headers

    async def _check_backend_ready(self) -> bool:
        if not self.backend_base:
            return False
        if self._backend_ready:
            return True
        health_path = f"{self.api_prefix}/health" if self.api_prefix else "/health"
        url = f"{self.backend_base}{health_path}"
        try:
            async with self.session.get(url, timeout=ClientTimeout(total=self.health_timeout)) as resp:
                if resp.status == 200:
                    self._backend_ready = True
                    return True
        except Exception:
            return False
        return False

    async def _handle_ui(self, request: web.Request, ui_action: str) -> web.Response:
        if ui_action == "redirect":
            raise web.HTTPFound(self.ui_prefix() + "/")
        if ui_action == "loading":
            return self.ui_assets.loading_response()

        if not self._backend_ready:
            ready = await self._check_backend_ready()
            if not ready:
                return self.ui_assets.loading_response()

        return self.ui_assets.index_response()

    async def _proxy(self, request: web.Request) -> web.StreamResponse:
        if not self.backend_base:
            return web.Response(status=503, text="Backend is not started")
        url = f"{self.backend_base}{request.path_qs}"
        req_headers = filter_headers(request.headers)
        req_headers.update(self._cors_headers(request))
        data = await request.read()

        async with self.session.request(
            request.method,
            url,
            headers=req_headers,
            data=data,
            allow_redirects=False,
        ) as resp:
            resp_headers = filter_headers(resp.headers)
            resp_headers.update(self._cors_headers(request))

            content_type = resp.headers.get("Content-Type", "")
            is_stream = "text/event-stream" in content_type

            if is_stream:
                stream_resp = web.StreamResponse(status=resp.status, headers=resp_headers)
                await stream_resp.prepare(request)
                async for chunk in resp.content.iter_chunked(4096):
                    await stream_resp.write(chunk)
                await stream_resp.write_eof()
                return stream_resp

            body = await resp.read()
            return web.Response(status=resp.status, body=body, headers=resp_headers)

    async def handler(self, request: web.Request) -> web.StreamResponse:
        if request.method == "OPTIONS":
            headers = self._cors_headers(request)
            headers.update(
                {
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                }
            )
            return web.Response(status=204, headers=headers)

        if not self.backend_base:
            setup_paths = self._setup_paths()
            if request.path in setup_paths:
                return self.ui_assets.setup_response()

        ui_paths = self._ui_paths()
        if request.path in ui_paths:
            return await self._handle_ui(request, ui_paths[request.path])

        if self.backend_base and request.path in self._setup_paths():
            raise web.HTTPFound(self.ui_prefix() + "/")

        if self.redirect_root and request.path == "/" and self.ui_prefix():
            raise web.HTTPFound(self.ui_prefix() + "/")

        if not self._is_proxy_path(request.path):
            return web.Response(status=404, text="Not Found")

        try:
            return await self._proxy(request)
        except Exception as exc:
            logging.exception("Proxy error")
            return web.Response(status=502, text=f"Bad Gateway: {exc}")

    async def close(self) -> None:
        await self.session.close()
