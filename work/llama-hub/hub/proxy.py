import asyncio
import json
import logging
from typing import Dict, Optional

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

from .ui import UIAssets
from rag import RagEngine

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
        rag_engine: Optional[RagEngine] = None,
        health_timeout: float = 0.5,
        ssl_verify: bool = True,
        redirect_root: bool = True,
    ) -> None:
        self.backend_base = backend_base.rstrip("/") if backend_base else None
        self.api_prefix = normalize_prefix(api_prefix)
        self.ui_assets = ui_assets
        self.rag_engine = rag_engine
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

    def _strip_api_prefix(self, path: str) -> str:
        if not self.api_prefix:
            return path
        if path == self.api_prefix:
            return ""
        if path.startswith(self.api_prefix + "/"):
            return path[len(self.api_prefix) :]
        return path

    def _is_generation_path(self, path: str) -> bool:
        stripped = self._strip_api_prefix(path)
        return stripped in ("/v1/chat/completions", "/v1/completions", "/v1/responses")

    def _is_json_request(self, request: web.Request) -> bool:
        content_type = request.headers.get("Content-Type", "")
        return "application/json" in content_type.lower()

    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text" and "text" in item:
                        parts.append(str(item.get("text")))
                    elif "text" in item:
                        parts.append(str(item.get("text")))
                    elif "content" in item and isinstance(item.get("content"), str):
                        parts.append(str(item.get("content")))
            return "\n".join([p for p in parts if p])
        if isinstance(content, dict):
            if "text" in content:
                return str(content.get("text"))
            if "content" in content and isinstance(content.get("content"), str):
                return str(content.get("content"))
        return ""

    def _extract_query_from_messages(self, messages) -> str:
        if not isinstance(messages, list):
            return ""
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            return self._extract_text(msg.get("content"))
        return ""

    def _extract_query(self, payload: Dict, path: str) -> str:
        stripped = self._strip_api_prefix(path)
        if stripped == "/v1/chat/completions":
            return self._extract_query_from_messages(payload.get("messages"))
        if stripped == "/v1/completions":
            prompt = payload.get("prompt")
            if isinstance(prompt, str):
                return prompt
            if isinstance(prompt, list) and prompt:
                tail = prompt[-1]
                if isinstance(tail, str):
                    return tail
            return ""
        if stripped == "/v1/responses":
            input_value = payload.get("input")
            if isinstance(input_value, str):
                return input_value
            if isinstance(input_value, list):
                for item in reversed(input_value):
                    if isinstance(item, str):
                        return item
                    if isinstance(item, dict):
                        role = item.get("role")
                        if role == "user" and "content" in item:
                            return self._extract_text(item.get("content"))
                        if item.get("type") in {"input_text", "text"} and "text" in item:
                            return str(item.get("text"))
                        if item.get("type") == "message" and "content" in item:
                            return self._extract_text(item.get("content"))
            if isinstance(input_value, dict):
                if "content" in input_value:
                    return self._extract_text(input_value.get("content"))
                if "text" in input_value:
                    return str(input_value.get("text"))
            return ""
        return ""

    def _build_context_block(self, results) -> str:
        lines = ["CONTEXT:"]
        for idx, item in enumerate(results, start=1):
            source = item.get("source_path", "")
            chunk_id = item.get("chunk_id", "")
            text = item.get("text", "")
            lines.append(f"[{idx}] source={source} chunk={chunk_id}")
            lines.append(str(text))
        return "\n".join(lines)

    def _inject_context(self, payload: Dict, path: str, context_block: str) -> bool:
        stripped = self._strip_api_prefix(path)
        if stripped == "/v1/chat/completions":
            messages = payload.get("messages")
            if not isinstance(messages, list):
                return False
            insert_at = 0
            while (
                insert_at < len(messages)
                and isinstance(messages[insert_at], dict)
                and messages[insert_at].get("role") == "system"
            ):
                insert_at += 1
            messages.insert(insert_at, {"role": "system", "content": context_block})
            payload["messages"] = messages
            return True
        if stripped == "/v1/completions":
            prompt = payload.get("prompt")
            if isinstance(prompt, str):
                payload["prompt"] = f"{context_block}\n\n{prompt}"
                return True
            if isinstance(prompt, list) and prompt:
                payload["prompt"] = [
                    f"{context_block}\n\n{p}" if isinstance(p, str) else p for p in prompt
                ]
                return True
            return False
        if stripped == "/v1/responses":
            instructions = payload.get("instructions")
            if instructions is None:
                payload["instructions"] = context_block
                return True
            if isinstance(instructions, str):
                if instructions.strip():
                    payload["instructions"] = instructions.rstrip() + "\n\n" + context_block
                else:
                    payload["instructions"] = context_block
                return True
            return False
        return False

    async def _maybe_rag_inject(self, request: web.Request, data: bytes) -> bytes:
        if not self.rag_engine or not self.rag_engine.enabled:
            return data
        if request.method != "POST":
            return data
        if not self._is_generation_path(request.path):
            return data
        if not self._is_json_request(request):
            return data
        if not data:
            return data

        logging.debug("RAG candidate: path=%s bytes=%s", request.path, len(data))

        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            return data

        query = self._extract_query(payload, request.path)
        if not query:
            logging.debug("RAG skip: no query extracted for %s", request.path)
            return data

        try:
            results = await asyncio.to_thread(self.rag_engine.retrieve, query)
        except Exception:
            logging.exception("RAG retrieval failed")
            return data
        if not results:
            logging.info("RAG retrieve returned 0 results for %s", request.path)
            return data

        context_block = self._build_context_block(results)
        updated = self._inject_context(payload, request.path, context_block)
        if not updated:
            return data

        logging.info(
            "RAG inject: path=%s context_chars=%s hits=%s",
            request.path,
            len(context_block),
            len(results),
        )
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

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
        data = await self._maybe_rag_inject(request, data)

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
