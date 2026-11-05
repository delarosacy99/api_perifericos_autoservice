"""Local hardware ACK journal. Separate from the financial database, atomically fsynced."""

import json
import os
from pathlib import Path

from .base import CashAcceptorProtocolError


class HardwareJournal:
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("stage") not in {
                "pending",
                "ack_intent",
                "acked",
            }:
                raise ValueError("stage")
            if not all(k in data for k in ("serial_number", "batch_id", "events", "notes")):
                raise ValueError("incomplete batch")
            if not isinstance(data["notes"], list) or not isinstance(data["events"], list):
                raise ValueError("invalid batch")
            return data
        except (ValueError, OSError, TypeError, KeyError) as exc:
            raise CashAcceptorProtocolError("El diario NV200 requiere revisión técnica.") from exc

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        if os.name != "nt":
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
