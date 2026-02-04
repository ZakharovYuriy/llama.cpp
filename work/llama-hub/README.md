# llama-hub (MVP)

Minimal Python wrapper that:

- starts `llama-server` as a subprocess
- serves the existing llama.cpp Web UI (single-file bundle)
- proxies API requests to the external `llama-server`, including SSE streaming

## Structure

- `hub/main.py` — CLI + process start + HTTP server
- `hub/proxy.py` — reverse-proxy (streaming)
- `hub/ui.py` — static UI handler (`index.html.gz`, `loading.html`)
- `hub/process.py` — `llama-server` lifecycle
- `static/` — UI assets copied from `tools/server/public/`

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

python -m hub.main \
  --server-bin /path/to/llama-server \
  -- --model /path/to/model.gguf --port 8080
```

Open:

- `http://127.0.0.1:8081/` (default)
- If you pass `--api-prefix /api` to `llama-server`, UI will be at `http://127.0.0.1:8081/api/`

### No-args setup mode

If you start without backend args, a setup page opens at `http://127.0.0.1:8081/` where you can:

- enter launch arguments
- choose or paste the model path
- set a model name (alias)

## Notes / limitations

- Backend UNIX sockets (`--host *.sock`) are not supported in this MVP.
- If `llama-server` uses TLS, set `--backend-scheme https` and optionally `--backend-ssl-no-verify`.
- This MVP only serves the bundled `index.html.gz` from `static/` (no build step).

## Updating UI

Copy the latest built UI bundle from llama.cpp:

- `tools/server/public/index.html.gz` → `static/index.html.gz`
- `tools/server/public/loading.html` → `static/loading.html`
