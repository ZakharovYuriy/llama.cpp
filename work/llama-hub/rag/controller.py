import logging
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .engine import RagConfig, RagEngine, index_is_ready, index_paths, load_manifest
from .state import RagState, apply_defaults, save_state

ALLOWED_SUFFIXES = {".txt", ".md"}


def normalize_path_str(value: str) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if "\\" in raw and "/" not in raw:
        raw = raw.replace("\\", "/")
    path = Path(raw)
    if not path.is_absolute() and ":" not in raw:
        candidate = Path("/") / path
        if candidate.exists():
            return str(candidate)
    return raw


class RagController:
    def __init__(self, state: RagState, state_path: Path) -> None:
        self._state = state
        self._state_path = state_path
        self._engine: Optional[RagEngine] = None
        self._lock = threading.Lock()
        self._building = False
        self._build_error: Optional[str] = None
        self._stats: Dict[str, int] = {"chunks": 0, "index_size": 0}
        self._init_engine()

    @property
    def enabled(self) -> bool:
        return self._state.enabled

    def can_inject(self) -> bool:
        return self.enabled and self.status() == "ready"

    def status(self) -> str:
        if not self._state.enabled:
            return "disabled"
        if self._building:
            return "building"
        if self._build_error:
            return "error"
        config = self._current_config()
        if not config:
            return "error"
        if index_is_ready(config):
            return "ready"
        return "not_built"

    def last_error(self) -> Optional[str]:
        if self._build_error:
            return self._build_error
        if self._state.enabled and not self._state.is_complete():
            return "RAG enabled but configuration is incomplete"
        return self._state.last_error

    def retrieve(self, query: str) -> List[Dict]:
        with self._lock:
            if not self.enabled or self._building:
                return []
            engine = self._engine
        if not engine:
            return []
        return engine.retrieve(query)

    def snapshot(self) -> Dict:
        status = self.status()
        config = self._config_payload()
        files = self.list_files()
        stats = dict(self._stats)
        if status == "ready" and self._engine:
            stats = self._engine.stats()
            self._stats = stats
        elif status != "ready":
            stats = self._manifest_stats()
        return {
            "enabled": self._state.enabled,
            "status": status,
            "last_error": self.last_error(),
            "config": config,
            "files": files,
            "stats": stats,
            "building": self._building,
        }

    def update_state(self, enabled: Optional[bool], config: Optional[Dict]) -> Dict:
        with self._lock:
            if enabled is not None:
                self._state.enabled = bool(enabled)
            if config:
                self._state.apply_config(config)
            self._normalize_paths_in_state()
            apply_defaults(self._state)
            self._build_error = None
            self._init_engine()
            if self._state.enabled:
                files = self.list_files()
                ready = False
                config_current = self._current_config()
                if config_current:
                    ready = index_is_ready(config_current)
                if not files:
                    self._state.enabled = False
                    self._state.last_error = "Cannot enable RAG: no documents in Docs Directory"
                elif not ready:
                    self._state.enabled = False
                    self._state.last_error = "Cannot enable RAG: index is not built yet"
            save_state(self._state, self._state_path)
        return self.snapshot()

    def start_build(self) -> Tuple[bool, Dict]:
        with self._lock:
            if self._building:
                return False, self.snapshot()
            self._building = True
            self._build_error = None
            save_state(self._state, self._state_path)

        thread = threading.Thread(target=self._build_worker, daemon=True)
        thread.start()
        return True, self.snapshot()

    def list_files(self) -> List[Dict]:
        docs_dir = self._docs_dir()
        if not docs_dir or not docs_dir.exists():
            return []
        files: List[Dict] = []
        for path in sorted(docs_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            rel = str(path.relative_to(docs_dir))
            stat = path.stat()
            files.append(
                {
                    "id": rel,
                    "name": rel,
                    "size": stat.st_size,
                    "updated_at": stat.st_mtime,
                }
            )
        return files

    def upload_files(self, items: Iterable[Tuple[str, bytes]]) -> Dict:
        docs_dir = self._require_docs_dir()
        saved = 0
        for filename, data in items:
            safe_name = Path(filename).name
            if not safe_name:
                continue
            if Path(safe_name).suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            dest = self._unique_dest(docs_dir, safe_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            saved += 1

        if saved:
            self._invalidate_index()
        return self.snapshot()

    def delete_files(self, ids: Iterable[str]) -> Dict:
        docs_dir = self._docs_dir()
        if not docs_dir or not docs_dir.exists():
            return self.snapshot()
        removed = 0
        for rel in ids:
            if not rel:
                continue
            target = (docs_dir / rel).resolve()
            if not self._is_within_dir(target, docs_dir):
                continue
            if target.exists() and target.is_file():
                target.unlink()
                removed += 1
        if removed:
            self._invalidate_index()
        return self.snapshot()

    def clear_files(self) -> Dict:
        docs_dir = self._docs_dir()
        if not docs_dir or not docs_dir.exists():
            return self.snapshot()
        removed = 0
        for path in docs_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            path.unlink()
            removed += 1
        if removed:
            self._invalidate_index()
        return self.snapshot()

    # ──────────────────────────────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────────────────────────────

    def _init_engine(self) -> None:
        config = self._current_config()
        if not config:
            self._engine = None
            self._stats = {"chunks": 0, "index_size": 0}
            return
        try:
            engine = RagEngine(config)
            if engine.load_index_if_available():
                self._stats = engine.stats()
            else:
                self._stats = self._manifest_stats()
            self._engine = engine
        except Exception as exc:
            logging.exception("RAG init failed")
            self._engine = None
            self._stats = {"chunks": 0, "index_size": 0}
            self._build_error = str(exc)
            self._state.last_error = str(exc)

    def _current_config(self) -> Optional[RagConfig]:
        if not self._state.is_complete():
            return None
        self._normalize_paths_in_state()
        return RagConfig(
            enabled=self._state.enabled,
            docs_dir=Path(self._state.docs_dir),
            emb_model_path=Path(self._state.emb_model_path),
            index_dir=Path(self._state.index_dir),
            top_k=self._state.top_k,
            chunk_size=self._state.chunk_size,
            chunk_overlap=self._state.chunk_overlap,
        ).resolved()

    def _config_payload(self) -> Dict:
        self._normalize_paths_in_state()
        return {
            "docs_dir": self._state.docs_dir,
            "emb_model_path": self._state.emb_model_path,
            "index_dir": self._state.index_dir,
            "top_k": self._state.top_k,
            "chunk_size": self._state.chunk_size,
            "chunk_overlap": self._state.chunk_overlap,
        }

    def _docs_dir(self) -> Optional[Path]:
        if not self._state.docs_dir:
            return None
        self._normalize_paths_in_state()
        return Path(self._state.docs_dir)

    def _require_docs_dir(self) -> Path:
        docs_dir = self._docs_dir()
        if not docs_dir:
            raise ValueError("docs_dir is not configured")
        docs_dir.mkdir(parents=True, exist_ok=True)
        return docs_dir

    def _unique_dest(self, docs_dir: Path, name: str) -> Path:
        dest = docs_dir / name
        if not dest.exists():
            return dest
        stem = dest.stem
        suffix = dest.suffix
        idx = 2
        while True:
            candidate = docs_dir / f"{stem}-{idx}{suffix}"
            if not candidate.exists():
                return candidate
            idx += 1

    def _is_within_dir(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _manifest_stats(self) -> Dict[str, int]:
        config = self._current_config()
        if not config:
            return {"chunks": 0, "index_size": 0}
        _index_path, _meta_path, manifest_path = index_paths(config)
        manifest = load_manifest(manifest_path) or {}
        chunk_count = int(manifest.get("chunk_count") or 0)
        return {"chunks": chunk_count, "index_size": 0}

    def _invalidate_index(self) -> None:
        config = self._current_config()
        if not config:
            return
        index_path, meta_path, manifest_path = index_paths(config)
        for path in (index_path, meta_path, manifest_path):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                logging.exception("Failed to remove index file %s", path)
        self._engine = RagEngine(config)
        self._stats = {"chunks": 0, "index_size": 0}

    def _build_worker(self) -> None:
        error: Optional[str] = None
        try:
            config = self._current_config()
            if not config:
                raise ValueError("RAG configuration is incomplete")
            if not config.docs_dir.exists():
                config.docs_dir.mkdir(parents=True, exist_ok=True)
            if not config.emb_model_path.exists():
                raise ValueError(f"emb_model_path not found: {config.emb_model_path}")
            engine = RagEngine(config)
            engine.build_index_now()
            self._engine = engine
            self._stats = engine.stats()
        except Exception as exc:
            logging.exception("RAG build failed")
            error = str(exc)
        finally:
            with self._lock:
                self._building = False
                self._build_error = error
                self._state.last_error = error
                save_state(self._state, self._state_path)

    def _normalize_paths_in_state(self) -> None:
        self._state.docs_dir = normalize_path_str(self._state.docs_dir)
        self._state.emb_model_path = normalize_path_str(self._state.emb_model_path)
        self._state.index_dir = normalize_path_str(self._state.index_dir)
        apply_defaults(self._state)
