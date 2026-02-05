import argparse
import asyncio
import logging
import shlex
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from aiohttp import web

from .model_state import (
    ModelConfig,
    ModelState,
    load_state as load_model_state,
    save_state as save_model_state,
    state_path as model_state_path,
)
from .process import ProcessManager
from .proxy import HubProxy
from .ui import UIAssets, resolve_static_dir
from rag import RagController, RagState, load_state as load_rag_state, save_state as save_rag_state, state_path as rag_state_path


def extract_flag(args: List[str], flag: str) -> Optional[str]:
    value = None
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
    return value


def has_flag(args: List[str], flags: List[str]) -> bool:
    for arg in args:
        for flag in flags:
            if arg == flag or arg.startswith(flag + "="):
                return True
    return False


def normalize_prefix(prefix: Optional[str]) -> str:
    if not prefix or prefix == "/":
        return ""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    if prefix.endswith("/"):
        prefix = prefix[:-1]
    return prefix


def infer_backend(args: List[str]) -> Tuple[str, int, str, Optional[bool]]:
    host = extract_flag(args, "--host") or "127.0.0.1"
    port_str = extract_flag(args, "--port") or "8080"
    api_prefix = extract_flag(args, "--api-prefix") or ""

    if host.endswith(".sock"):
        raise ValueError("UNIX socket backends are not supported in this MVP")

    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(f"Invalid --port value: {port_str}") from exc

    if host in ("0.0.0.0", "::"):
        connect_host = "127.0.0.1"
    else:
        connect_host = host

    ssl_detected = None
    if extract_flag(args, "--ssl-key-file") or extract_flag(args, "--ssl-cert-file"):
        ssl_detected = True

    return connect_host, port, normalize_prefix(api_prefix), ssl_detected


def parse_args() -> Tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(description="llama-hub: UI + proxy wrapper for llama-server")
    parser.add_argument("--server-bin", default=None, help="Path to llama-server binary")
    parser.add_argument("--ui-host", default="127.0.0.1", help="Host for UI server")
    parser.add_argument("--ui-port", type=int, default=8081, help="Port for UI server")
    parser.add_argument("--static-dir", default=None, help="Path to UI static files (index.html.gz)")
    parser.add_argument(
        "--backend-scheme",
        choices=["http", "https"],
        default=None,
        help="Override backend scheme (auto-detects https if ssl flags are present)",
    )
    parser.add_argument(
        "--backend-ssl-verify",
        dest="backend_ssl_verify",
        action="store_true",
        default=True,
        help="Verify backend TLS certificates (only used for https)",
    )
    parser.add_argument(
        "--backend-ssl-no-verify",
        dest="backend_ssl_verify",
        action="store_false",
        help="Disable backend TLS verification (only used for https)",
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=0.5,
        help="Seconds to wait for /health when deciding whether to show loading page",
    )
    parser.add_argument(
        "--no-redirect-root",
        action="store_true",
        help="Disable redirect from / to api-prefix when api-prefix is set",
    )
    parser.add_argument(
        "--rag-enabled",
        type=int,
        default=1,
        choices=[0, 1],
        help="Enable local RAG injection (0 or 1)",
    )
    parser.add_argument("--docs-dir", default="/data/docsForLLM", help="Path to .txt/.md docs for RAG")
    parser.add_argument(
        "--emb-model-path",
        default="/data/multilingual-e5-small",
        help="Path to multilingual-e5-small model",
    )
    parser.add_argument("--index-dir", default="/data/index", help="Path to FAISS index directory")
    parser.add_argument("--rag-top-k", type=int, default=5, help="Top-k chunks for RAG retrieval")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Chunk size in characters")
    parser.add_argument("--chunk-overlap", type=int, default=200, help="Chunk overlap in characters")

    args, server_args = parser.parse_known_args()
    if server_args and server_args[0] == "--":
        server_args = server_args[1:]

    return args, server_args


def resolve_server_bin(cli_value: Optional[str]) -> Optional[Path]:
    if cli_value:
        return Path(cli_value).resolve()

    default_path = Path("/workspace/build/bin/llama-server")
    if default_path.exists():
        return default_path

    found = shutil.which("llama-server")
    if found:
        return Path(found).resolve()

    return None


def build_server_args(launch_args: str, model_path: str, model_name: str) -> List[str]:
    args = shlex.split(launch_args or "")

    if model_path and not has_flag(args, ["--model", "-m"]):
        args += ["--model", model_path]

    if model_name and not has_flag(args, ["--alias", "-a"]):
        args += ["--alias", model_name]

    return args


MODEL_STATUS_NOT_CONFIGURED = "not_configured"
MODEL_STATUS_STOPPED = "stopped"
MODEL_STATUS_RESTARTING = "restarting"
MODEL_STATUS_READY = "ready"
MODEL_STATUS_ERROR = "error"


def strip_model_flags(args: List[str]) -> List[str]:
    flags = ("--model", "-m", "--alias", "-a")
    cleaned: List[str] = []
    skip_next = False
    for idx, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in flags:
            if idx + 1 < len(args):
                skip_next = True
            continue
        if any(arg.startswith(flag + "=") for flag in flags):
            continue
        cleaned.append(arg)
    return cleaned


def model_config_from_server_args(server_args: List[str]) -> ModelConfig:
    model_path = extract_flag(server_args, "--model") or extract_flag(server_args, "-m") or ""
    model_name = extract_flag(server_args, "--alias") or extract_flag(server_args, "-a") or ""
    stripped = strip_model_flags(server_args)
    launch_args = shlex.join(stripped)
    return ModelConfig(model_name=model_name, model_path=model_path, launch_args=launch_args)


def model_config_from_payload(payload: dict) -> ModelConfig:
    model_name = str(payload.get("modelName") or "")
    model_path = str(payload.get("modelPath") or "")
    launch_args = str(payload.get("launchArgs") or "")
    return ModelConfig(model_name=model_name, model_path=model_path, launch_args=launch_args)


def model_config_payload(state: ModelState) -> dict:
    return {
        "modelName": state.config.model_name,
        "modelPath": state.config.model_path,
        "launchArgs": state.config.launch_args,
    }


def model_status_payload(state: ModelState) -> dict:
    return {
        "status": state.status,
        "lastError": state.last_error,
        "updatedAt": state.updated_at,
        "lastRestartAt": state.last_restart_at,
    }


def model_state_payload(state: ModelState) -> dict:
    payload = model_status_payload(state)
    payload["config"] = model_config_payload(state)
    return payload


def build_ui_url(ui_host: str, ui_port: int, api_prefix: str) -> str:
    prefix = normalize_prefix(api_prefix)
    return f"http://{ui_host}:{ui_port}{prefix}/"


def extract_cli_args(raw_args: List[str]) -> List[str]:
    if "--" in raw_args:
        return raw_args[: raw_args.index("--")]
    return raw_args


def detect_rag_cli_flags(raw_args: List[str]) -> bool:
    rag_flags = [
        "--rag-enabled",
        "--docs-dir",
        "--emb-model-path",
        "--index-dir",
        "--rag-top-k",
        "--chunk-size",
        "--chunk-overlap",
    ]
    return has_flag(raw_args, rag_flags)


@dataclass
class RuntimeState:
    server_bin: Optional[Path]
    proc: Optional[ProcessManager] = None
    backend_base: Optional[str] = None
    api_prefix: str = ""

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.is_running()


async def run() -> None:
    args, server_args = parse_args()

    server_bin = resolve_server_bin(args.server_bin)
    if args.server_bin and (not server_bin or not server_bin.exists()):
        raise SystemExit(f"llama-server binary not found: {args.server_bin}")

    ui_assets = UIAssets(resolve_static_dir(args.static_dir))

    raw_cli_args = extract_cli_args(sys.argv[1:])
    rag_cli_explicit = detect_rag_cli_flags(raw_cli_args)
    rag_state_file = rag_state_path()

    if rag_cli_explicit:
        rag_state = RagState(
            enabled=bool(args.rag_enabled),
            docs_dir=str(args.docs_dir or ""),
            emb_model_path=str(args.emb_model_path or ""),
            index_dir=str(args.index_dir or ""),
            top_k=int(args.rag_top_k),
            chunk_size=int(args.chunk_size),
            chunk_overlap=int(args.chunk_overlap),
        )
        save_rag_state(rag_state, rag_state_file)
    else:
        rag_state = load_rag_state(rag_state_file)

    rag_controller = RagController(rag_state, rag_state_file)
    proxy = HubProxy(
        backend_base=None,
        api_prefix="",
        ui_assets=ui_assets,
        rag_controller=rag_controller,
        health_timeout=args.health_timeout,
        ssl_verify=args.backend_ssl_verify,
        redirect_root=not args.no_redirect_root,
    )

    state = RuntimeState(server_bin=server_bin)
    model_state_file = model_state_path()
    model_state = load_model_state(model_state_file)
    restart_lock = asyncio.Lock()

    def model_is_configured(config: ModelConfig) -> bool:
        return bool(config.model_path)

    def update_model_state(
        *,
        config: Optional[ModelConfig] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
        clear_error: bool = False,
        mark_restart: bool = False,
    ) -> None:
        if config is not None:
            model_state.config = config
        if status is not None:
            model_state.status = status
        if clear_error:
            model_state.last_error = None
        elif error is not None:
            model_state.last_error = error
        if mark_restart:
            model_state.last_restart_at = time.time()
        save_model_state(model_state, model_state_file)

    if not server_args:
        if model_is_configured(model_state.config):
            model_state.status = MODEL_STATUS_STOPPED
        else:
            model_state.status = MODEL_STATUS_NOT_CONFIGURED
        save_model_state(model_state, model_state_file)

    def start_backend(server_args_local: List[str]) -> str:
        nonlocal state

        if state.is_running():
            raise RuntimeError("llama-server is already running")
        if not state.server_bin or not state.server_bin.exists():
            raise RuntimeError("llama-server binary not found")

        backend_host, backend_port, api_prefix, ssl_detected = infer_backend(server_args_local)

        scheme = args.backend_scheme
        if scheme is None:
            scheme = "https" if ssl_detected else "http"

        backend_base = f"{scheme}://{backend_host}:{backend_port}"
        proxy.set_backend(backend_base, api_prefix)

        proc = ProcessManager([str(state.server_bin)] + server_args_local)
        proc.start()
        state.proc = proc
        state.backend_base = backend_base
        state.api_prefix = api_prefix

        return build_ui_url(args.ui_host, args.ui_port, api_prefix)

    async def setup_start(request: web.Request) -> web.Response:
        if state.is_running():
            return web.json_response({"success": False, "error": "Server already running"}, status=409)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        launch_args = str(payload.get("launchArgs", ""))
        model_path = str(payload.get("modelPath", ""))
        model_name = str(payload.get("modelName", ""))

        config = ModelConfig(model_name=model_name, model_path=model_path, launch_args=launch_args)

        if not model_path:
            return web.json_response({"success": False, "error": "Model path is required"}, status=400)

        server_args_local = build_server_args(launch_args, model_path, model_name)

        try:
            ui_url = start_backend(server_args_local)
        except Exception as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)

        update_model_state(
            config=config,
            status=MODEL_STATUS_READY,
            clear_error=True,
            mark_restart=True,
        )
        return web.json_response({"success": True, "ui_url": ui_url})

    async def model_config_get(request: web.Request) -> web.Response:
        return web.json_response(model_config_payload(model_state))

    async def model_status_get(request: web.Request) -> web.Response:
        return web.json_response(model_status_payload(model_state))

    async def model_config_update(request: web.Request) -> web.Response:
        if restart_lock.locked():
            return web.json_response({"error": "Model restart already in progress"}, status=409)
        if model_state.status == MODEL_STATUS_RESTARTING:
            return web.json_response({"error": "Model restart already in progress"}, status=409)

        if not state.server_bin or not state.server_bin.exists():
            return web.json_response({"error": "llama-server binary not found"}, status=400)

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        config = model_config_from_payload(payload if isinstance(payload, dict) else {})
        if not config.model_path:
            return web.json_response({"error": "Model path is required"}, status=400)

        update_model_state(
            config=config,
            status=MODEL_STATUS_RESTARTING,
            clear_error=True,
            mark_restart=True,
        )

        async def restart_worker() -> None:
            async with restart_lock:
                try:
                    if state.proc:
                        await asyncio.to_thread(state.proc.terminate)
                    server_args_local = build_server_args(
                        config.launch_args, config.model_path, config.model_name
                    )
                    start_backend(server_args_local)
                    update_model_state(status=MODEL_STATUS_READY, clear_error=True)
                except Exception as exc:
                    update_model_state(status=MODEL_STATUS_ERROR, error=str(exc))

        asyncio.create_task(restart_worker())
        return web.json_response(model_state_payload(model_state), status=202)

    async def rag_state_get(request: web.Request) -> web.Response:
        return web.json_response(rag_controller.snapshot())

    async def rag_state_update(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        enabled = payload.get("enabled")
        config = payload.get("config") if isinstance(payload.get("config"), dict) else None
        try:
            state = rag_controller.update_state(enabled, config)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(state)

    async def rag_files_list(request: web.Request) -> web.Response:
        return web.json_response({"files": rag_controller.list_files()})

    async def rag_files_upload(request: web.Request) -> web.Response:
        items = []
        try:
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if not part:
                    break
                if part.name not in {"file", "files"}:
                    continue
                filename = part.filename or ""
                data = await part.read(decode=False)
                items.append((filename, data))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        try:
            state = rag_controller.upload_files(items)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(state)

    async def rag_files_delete(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        ids = payload.get("ids") or payload.get("files") or []
        if not isinstance(ids, list):
            ids = []
        state = rag_controller.delete_files(ids)
        return web.json_response(state)

    async def rag_files_clear(request: web.Request) -> web.Response:
        state = rag_controller.clear_files()
        return web.json_response(state)

    async def rag_build(request: web.Request) -> web.Response:
        started, state = rag_controller.start_build()
        if not started:
            return web.json_response(state, status=409)
        return web.json_response(state)

    app = web.Application()
    app.router.add_post("/setup/start", setup_start)
    app.router.add_get("/model/config", model_config_get)
    app.router.add_post("/model/config", model_config_update)
    app.router.add_get("/model/status", model_status_get)
    app.router.add_get("/rag/state", rag_state_get)
    app.router.add_post("/rag/state", rag_state_update)
    app.router.add_get("/rag/files", rag_files_list)
    app.router.add_post("/rag/files", rag_files_upload)
    app.router.add_post("/rag/files/delete", rag_files_delete)
    app.router.add_post("/rag/files/clear", rag_files_clear)
    app.router.add_post("/rag/build", rag_build)
    app.router.add_route("*", "/{tail:.*}", proxy.handler)

    async def on_cleanup(app: web.Application) -> None:
        await proxy.close()
        if state.proc:
            state.proc.terminate()

    app.on_cleanup.append(on_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.ui_host, args.ui_port)
    await site.start()

    if server_args:
        ui_url = start_backend(server_args)
        update_model_state(
            config=model_config_from_server_args(server_args),
            status=MODEL_STATUS_READY,
            clear_error=True,
            mark_restart=True,
        )
        logging.info("UI available at %s", ui_url)
    else:
        setup_url = f"http://{args.ui_host}:{args.ui_port}/"
        logging.info("Setup UI available at %s", setup_url)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
