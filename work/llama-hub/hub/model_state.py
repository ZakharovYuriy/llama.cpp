import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STATE_FILE_ENV = "LLAMA_HUB_MODEL_STATE"
DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "model_state.json"


@dataclass
class ModelConfig:
    model_name: str = ""
    model_path: str = ""
    launch_args: str = ""

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "model_path": self.model_path,
            "launch_args": self.launch_args,
        }


@dataclass
class ModelState:
    config: ModelConfig = field(default_factory=ModelConfig)
    status: str = "not_configured"
    last_error: Optional[str] = None
    updated_at: Optional[float] = None
    last_restart_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "status": self.status,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
            "last_restart_at": self.last_restart_at,
        }


def state_path() -> Path:
    override = os.environ.get(STATE_FILE_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_STATE_FILE


def load_state(path: Optional[Path] = None) -> ModelState:
    target = path or state_path()
    if not target.exists():
        return ModelState()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return ModelState()

    state = ModelState()
    if isinstance(data, dict):
        config_data = data.get("config") if isinstance(data.get("config"), dict) else {}
        if isinstance(config_data, dict):
            state.config.model_name = str(config_data.get("model_name", state.config.model_name) or "")
            state.config.model_path = str(config_data.get("model_path", state.config.model_path) or "")
            state.config.launch_args = str(config_data.get("launch_args", state.config.launch_args) or "")
        state.status = str(data.get("status", state.status) or state.status)
        state.last_error = data.get("last_error")
        state.updated_at = data.get("updated_at")
        state.last_restart_at = data.get("last_restart_at")
    return state


def save_state(state: ModelState, path: Optional[Path] = None) -> None:
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    target.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
