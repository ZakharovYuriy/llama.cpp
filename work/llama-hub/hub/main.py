import argparse
import asyncio
import logging
import shlex
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from aiohttp import web

from .process import ProcessManager
from .proxy import HubProxy
from .ui import UIAssets, resolve_static_dir
from rag import RagConfig, RagEngine


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


def build_ui_url(ui_host: str, ui_port: int, api_prefix: str) -> str:
    prefix = normalize_prefix(api_prefix)
    return f"http://{ui_host}:{ui_port}{prefix}/"


def build_rag_engine(args: argparse.Namespace) -> Optional[RagEngine]:
    if not args.rag_enabled:
        logging.info("RAG disabled")
        return None

    missing = []
    if not args.docs_dir:
        missing.append("--docs-dir")
    if not args.emb_model_path:
        missing.append("--emb-model-path")
    if not args.index_dir:
        missing.append("--index-dir")
    if missing:
        raise SystemExit(f"RAG enabled but missing required args: {', '.join(missing)}")

    config = RagConfig(
        enabled=True,
        docs_dir=Path(args.docs_dir),
        emb_model_path=Path(args.emb_model_path),
        index_dir=Path(args.index_dir),
        top_k=args.rag_top_k,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    config = config.resolved()

    logging.info(
        "RAG enabled: docs_dir=%s emb_model_path=%s index_dir=%s top_k=%s chunk_size=%s chunk_overlap=%s",
        config.docs_dir,
        config.emb_model_path,
        config.index_dir,
        config.top_k,
        config.chunk_size,
        config.chunk_overlap,
    )

    if not config.docs_dir.exists():
        raise SystemExit(f"docs-dir not found: {config.docs_dir}")
    if not config.emb_model_path.exists():
        raise SystemExit(f"emb-model-path not found: {config.emb_model_path}")
    if config.index_dir.exists() and not config.index_dir.is_dir():
        raise SystemExit(f"index-dir is not a directory: {config.index_dir}")
    if config.top_k < 1:
        raise SystemExit("--rag-top-k must be >= 1")
    if config.chunk_size < 1:
        raise SystemExit("--chunk-size must be >= 1")
    if config.chunk_overlap < 0:
        raise SystemExit("--chunk-overlap must be >= 0")

    rag_engine = RagEngine(config)
    rag_engine.ensure_ready()
    stats = rag_engine.stats()
    logging.info("RAG ready: chunks=%s index_size=%s", stats["chunks"], stats["index_size"])
    return rag_engine


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
    rag_engine = build_rag_engine(args)
    proxy = HubProxy(
        backend_base=None,
        api_prefix="",
        ui_assets=ui_assets,
        rag_engine=rag_engine,
        health_timeout=args.health_timeout,
        ssl_verify=args.backend_ssl_verify,
        redirect_root=not args.no_redirect_root,
    )

    state = RuntimeState(server_bin=server_bin)

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

        if not model_path:
            return web.json_response({"success": False, "error": "Model path is required"}, status=400)

        server_args_local = build_server_args(launch_args, model_path, model_name)

        try:
            ui_url = start_backend(server_args_local)
        except Exception as exc:
            return web.json_response({"success": False, "error": str(exc)}, status=400)

        return web.json_response({"success": True, "ui_url": ui_url})

    app = web.Application()
    app.router.add_post("/setup/start", setup_start)
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
