"""WorkspaceManager — per-task isolated directories on shared volume."""
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog
from django.conf import settings

log = structlog.get_logger(__name__)


class WorkspaceManager:
    """Creates and manages isolated workspace directories per task.

    Lives under ``WORKSPACE_ROOT`` (default ``/workspaces``). Each task gets
    a directory named ``{taskId}_{ts}_{trace[:8]}`` with mode 0o700.
    """

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or settings.WORKSPACE_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, task_id: str, trace_id: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        safe_task = "".join(c if c.isalnum() or c in "-_." else "_" for c in task_id)
        path = self.root / f"{safe_task}_{ts}_{trace_id[:8]}"
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        log.info("workspace.created", path=str(path), task_id=task_id, trace_id=trace_id)
        return path

    def destroy(self, path: Path) -> None:
        if not path.exists():
            return
        resolved = path.resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            log.error("workspace.destroy.refused", path=str(path), reason="outside_root")
            raise ValueError(f"Refusing to destroy path outside workspace root: {path}")
        shutil.rmtree(path, ignore_errors=True)
        log.info("workspace.destroyed", path=str(path))

    def cleanup_expired(self, ttl_hours: int = 24) -> int:
        cutoff = time.time() - (ttl_hours * 3600)
        removed = 0
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            if path.stat().st_mtime < cutoff:
                try:
                    shutil.rmtree(path)
                    removed += 1
                except OSError:
                    log.warning("workspace.cleanup.failed", path=str(path))
        log.info("workspace.cleanup.completed", removed=removed)
        return removed
