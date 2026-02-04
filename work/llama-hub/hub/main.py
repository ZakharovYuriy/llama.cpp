import argparse
import asyncio
import logging
import signal
from pathlib import Path
from typing import List, Optional, Tuple

from aiohttp import web

from .process import ProcessManager
from .proxy import HubProxy
from .ui import UIAssets, resolve_static_dir


def extract_flag(args: List[str], flag: str) -> Optional[str]:
    value = None
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
    return value


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
    parser.add_argument("--server-bin", required=True, help="Path to llama-server binary")
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

    args, server_args = parser.parse_known_args()
    if server_args and server_args[0] == "--":
        server_args = server_args[1:]

    return args, server_args


async def run() -> None:
    args, server_args = parse_args()

    server_bin = Path(args.server_bin).resolve()
    if not server_bin.exists():
        raise SystemExit(f"llama-server binary not found: {server_bin}")

    backend_host, backend_port, api_prefix, ssl_detected = infer_backend(server_args)

    scheme = args.backend_scheme
    if scheme is None:
        scheme = "https" if ssl_detected else "http"

    backend_base = f"{scheme}://{backend_host}:{backend_port}"

    ui_assets = UIAssets(resolve_static_dir(args.static_dir))
    proxy = HubProxy(
        backend_base=backend_base,
        api_prefix=api_prefix,
        ui_assets=ui_assets,
        health_timeout=args.health_timeout,
        ssl_verify=args.backend_ssl_verify,
        redirect_root=not args.no_redirect_root,
    )

    proc = ProcessManager([str(server_bin)] + server_args)
    proc.start()

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", proxy.handler)

    async def on_cleanup(app: web.Application) -> None:
        await proxy.close()

    app.on_cleanup.append(on_cleanup)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.ui_host, args.ui_port)
    await site.start()

    ui_prefix = proxy.ui_prefix() or ""
    base_url = f"http://{args.ui_host}:{args.ui_port}{ui_prefix}/"
    logging.info("UI available at %s", base_url)

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
        proc.terminate()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
