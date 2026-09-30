"""Arka plan iş yöneticisi (CFD, optimizasyon, kurulum adımları).

Ağır işler (CFD, Docker imaj indirme ...) ``exclusive=True`` ile tek bir kuyrukta
sırayla çalışır; böylece aynı anda iki CFD tüm çekirdekleri paylaşmaz.
"""

from __future__ import annotations

import collections
import os
import queue
import signal
import subprocess
import threading
import time
import traceback
import uuid
from typing import Callable


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, title: str, meta: dict | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.meta = meta or {}
        self.status = "queued"   # queued | running | done | failed | cancelled
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self.progress = 0.0
        self.stage = "Kuyrukta"
        self.logs: collections.deque[tuple[int, str]] = collections.deque(maxlen=4000)
        self._seq = 0
        self.data: dict = {}
        self.result = None
        self.error: str | None = None
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ api
    def log(self, msg: str) -> None:
        with self._lock:
            for line in str(msg).splitlines() or [""]:
                self._seq += 1
                self.logs.append((self._seq, line))

    def set(self, progress: float | None = None, stage: str | None = None, **data) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if stage is not None:
            if stage != self.stage:
                self.log(f"▶ {stage}")
            self.stage = stage
        self.data.update(data)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def run_cmd(self, argv: list[str], cwd=None, env=None, timeout: float | None = None,
                on_line: Callable[[str], None] | None = None) -> int:
        """Komutu çalıştırıp çıktısını satır satır iş günlüğüne aktarır."""
        self.log("$ " + " ".join(argv))
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                start_new_session=True)
        start = time.time()

        def reader():
            for line in proc.stdout:
                line = line.rstrip("\n")
                # docker pull ilerleme satırlarında \r kullanır
                for part in line.split("\r"):
                    if part.strip():
                        self.log(part)
                        if on_line:
                            on_line(part)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        while proc.poll() is None:
            if self.cancel_event.is_set() or (timeout and time.time() - start > timeout):
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=20)
                t.join(timeout=2)
                if self.cancel_event.is_set():
                    raise Cancelled()
                raise RuntimeError(f"Zaman aşımı: {' '.join(argv)}")
            time.sleep(0.2)
        t.join(timeout=5)
        return proc.returncode

    def to_dict(self, log_since: int = 0, max_log: int = 400) -> dict:
        with self._lock:
            logs = [(i, s) for i, s in self.logs if i > log_since][-max_log:]
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "meta": self.meta,
            "status": self.status, "progress": self.progress, "stage": self.stage,
            "created": self.created, "started": self.started, "finished": self.finished,
            "elapsed": ((self.finished or time.time()) - self.started) if self.started else 0.0,
            "logs": logs, "last_log": self._seq, "data": self.data, "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def submit(self, kind: str, title: str, fn: Callable[[Job], object], exclusive: bool = True,
               meta: dict | None = None) -> Job:
        job = Job(kind, title, meta)
        self.jobs[job.id] = job
        if exclusive:
            self._queue.put((job, fn))
        else:
            threading.Thread(target=self._execute, args=(job, fn), daemon=True).start()
        return job

    def _loop(self):
        while True:
            job, fn = self._queue.get()
            if job.cancel_event.is_set():
                job.status, job.stage, job.finished = "cancelled", "İptal edildi", time.time()
                continue
            self._execute(job, fn)

    @staticmethod
    def _execute(job: Job, fn):
        job.status, job.started, job.stage = "running", time.time(), "Başladı"
        try:
            job.result = fn(job)
            job.status, job.progress = "done", 1.0
            job.stage = "Tamamlandı"
        except Cancelled:
            job.status, job.stage = "cancelled", "İptal edildi"
            job.log("İş kullanıcı tarafından iptal edildi.")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "İptal edildi" in msg:
                job.status, job.stage = "cancelled", "İptal edildi"
            else:
                job.status, job.stage, job.error = "failed", "Hata", msg
                job.log("HATA: " + msg)
                job.log(traceback.format_exc())
        finally:
            job.finished = time.time()

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status in ("done", "failed", "cancelled"):
            return False
        job.cancel_event.set()
        return True

    def list(self) -> list[dict]:
        out = []
        for j in sorted(self.jobs.values(), key=lambda j: j.created, reverse=True):
            d = j.to_dict(max_log=0)
            d.pop("logs")
            d.pop("data")
            out.append(d)
        return out
