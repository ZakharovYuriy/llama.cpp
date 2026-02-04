import logging
import subprocess
import time
from typing import Iterable, Optional


class ProcessManager:
    def __init__(self, cmd: Iterable[str], cwd: Optional[str] = None, env: Optional[dict] = None):
        self.cmd = list(cmd)
        self.cwd = cwd
        self.env = env
        self.proc: Optional[subprocess.Popen] = None

    def start(self) -> subprocess.Popen:
        if self.proc and self.proc.poll() is None:
            raise RuntimeError("Process already running")
        logging.info("Starting llama-server: %s", " ".join(self.cmd))
        self.proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=None,
            stderr=None,
        )
        return self.proc

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def terminate(self, timeout: float = 5.0) -> None:
        if not self.proc:
            return
        if self.proc.poll() is not None:
            return
        logging.info("Stopping llama-server (pid=%s)", self.proc.pid)
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logging.warning("llama-server did not exit in time; killing")
            self.proc.kill()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logging.error("llama-server still alive after kill")

    def wait(self) -> Optional[int]:
        if not self.proc:
            return None
        return self.proc.wait()
