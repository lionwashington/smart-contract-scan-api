"""
异步任务状态存储（进程内内存字典）。
MVP 阶段够用；多实例部署时换 Redis。
"""
import time
from typing import Any, Optional
from threading import Lock


class TaskStore:
    def __init__(self, ttl_seconds: int = 3600):
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self._ttl = ttl_seconds

    def create(self, scan_id: str) -> None:
        with self._lock:
            self._data[scan_id] = {
                "status": "queued",
                "result": None,
                "error": None,
                "created_at": time.time(),
            }

    def mark_running(self, scan_id: str) -> None:
        with self._lock:
            if scan_id in self._data:
                self._data[scan_id]["status"] = "running"

    def set_result(self, scan_id: str, result: Any) -> None:
        with self._lock:
            if scan_id in self._data:
                self._data[scan_id]["status"] = "completed"
                self._data[scan_id]["result"] = result

    def set_error(self, scan_id: str, error: str) -> None:
        with self._lock:
            if scan_id in self._data:
                self._data[scan_id]["status"] = "failed"
                self._data[scan_id]["error"] = error

    def get(self, scan_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            self._evict_expired()
            return self._data.get(scan_id)

    def _evict_expired(self) -> None:
        cutoff = time.time() - self._ttl
        expired = [k for k, v in self._data.items() if v["created_at"] < cutoff]
        for k in expired:
            del self._data[k]


_store = TaskStore()


def get_task_store() -> TaskStore:
    return _store
