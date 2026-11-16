"""Bus event sederhana untuk Server-Sent Events (SSE).

Setiap klien admin punya antrean sendiri; pengirim tidak pernah diblokir
kalau klien lambat (antrean penuh = data terlama dibuang).
"""
from __future__ import annotations

import json
import queue
import threading


class Bus:
    def __init__(self, ukuran=50):
        self._klien: set[queue.Queue] = set()
        self._kunci = threading.Lock()
        self._ukuran = ukuran

    def daftar(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._ukuran)
        with self._kunci:
            self._klien.add(q)
        return q

    def lepas(self, q: queue.Queue):
        with self._kunci:
            self._klien.discard(q)

    def siar(self, data: dict):
        pesan = json.dumps(data, default=str)
        with self._kunci:
            klien = list(self._klien)
        for q in klien:
            try:
                q.put_nowait(pesan)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(pesan)
                except queue.Empty:
                    pass

    @property
    def jumlah_klien(self):
        with self._kunci:
            return len(self._klien)
