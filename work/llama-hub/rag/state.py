import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

STATE_FILE_ENV = "LLAMA_HUB_RAG_STATE"
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "rag_state.json"

DEFAULT_DOCS_DIR = "/data/docsForLLM"
DEFAULT_EMB_MODEL_PATH = "/data/multilingual-e5-small"
DEFAULT_INDEX_DIR = "/data/index"
DEFAULT_TOP_K = 5
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200


@dataclass
class RagState:
    enabled: bool = False
    docs_dir: str = DEFAULT_DOCS_DIR
    emb_model_path: str = DEFAULT_EMB_MODEL_PATH
    index_dir: str = DEFAULT_INDEX_DIR
    top_k: int = DEFAULT_TOP_K
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    last_status: str = "disabled"
    last_error: Optional[str] = None
    updated_at: Optional[float] = None

    def is_complete(self) -> bool:
        return bool(self.docs_dir and self.emb_model_path and self.index_dir)

    def to_dict(self) -> dict:
        return asdict(self)

    def apply_config(self, config: dict) -> None:
        for key in ("docs_dir", "emb_model_path", "index_dir"):
            if key in config and config[key] is not None:
                value = str(config[key]).strip()
                if not value:
                    value = _default_path_for_key(key)
                setattr(self, key, value)
        for key in ("top_k", "chunk_size", "chunk_overlap"):
            if key in config and config[key] is not None:
                value = int(config[key])
                if value <= 0 and key in ("top_k", "chunk_size"):
                    value = _default_number_for_key(key)
                if value < 0 and key == "chunk_overlap":
                    value = _default_number_for_key(key)
                setattr(self, key, value)


def state_path() -> Path:
    override = os.environ.get(STATE_FILE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_STATE_FILE


def load_state(path: Optional[Path] = None) -> RagState:
    target = path or state_path()
    if not target.exists():
        return RagState()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return RagState()

    state = RagState()
    if isinstance(data, dict):
        state.enabled = bool(data.get("enabled", state.enabled))
        state.docs_dir = _coerce_path_value(data.get("docs_dir"), DEFAULT_DOCS_DIR)
        state.emb_model_path = _coerce_path_value(data.get("emb_model_path"), DEFAULT_EMB_MODEL_PATH)
        state.index_dir = _coerce_path_value(data.get("index_dir"), DEFAULT_INDEX_DIR)
        state.top_k = _coerce_int_value(data.get("top_k"), DEFAULT_TOP_K, allow_zero=False)
        state.chunk_size = _coerce_int_value(data.get("chunk_size"), DEFAULT_CHUNK_SIZE, allow_zero=False)
        state.chunk_overlap = _coerce_int_value(
            data.get("chunk_overlap"), DEFAULT_CHUNK_OVERLAP, allow_zero=True
        )
        state.last_status = str(data.get("last_status", state.last_status) or state.last_status)
        state.last_error = data.get("last_error")
        state.updated_at = data.get("updated_at")
    apply_defaults(state)
    return state


def save_state(state: RagState, path: Optional[Path] = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    target.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def apply_defaults(state: RagState) -> None:
    if not state.docs_dir:
        state.docs_dir = DEFAULT_DOCS_DIR
    if not state.emb_model_path:
        state.emb_model_path = DEFAULT_EMB_MODEL_PATH
    if not state.index_dir:
        state.index_dir = DEFAULT_INDEX_DIR
    if state.top_k <= 0:
        state.top_k = DEFAULT_TOP_K
    if state.chunk_size <= 0:
        state.chunk_size = DEFAULT_CHUNK_SIZE
    if state.chunk_overlap < 0:
        state.chunk_overlap = DEFAULT_CHUNK_OVERLAP


def _coerce_path_value(value: Optional[str], default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _coerce_int_value(value: Optional[int], default: int, allow_zero: bool) -> int:
    try:
        val = int(value)  # type: ignore[arg-type]
    except Exception:
        return default
    if allow_zero:
        return val if val >= 0 else default
    return val if val > 0 else default


def _default_path_for_key(key: str) -> str:
    if key == "docs_dir":
        return DEFAULT_DOCS_DIR
    if key == "emb_model_path":
        return DEFAULT_EMB_MODEL_PATH
    if key == "index_dir":
        return DEFAULT_INDEX_DIR
    return ""


def _default_number_for_key(key: str) -> int:
    if key == "top_k":
        return DEFAULT_TOP_K
    if key == "chunk_size":
        return DEFAULT_CHUNK_SIZE
    if key == "chunk_overlap":
        return DEFAULT_CHUNK_OVERLAP
    return 0
