"""Datasets the viewer is working on.

A session owns one directory of CSVs plus a private `runs/` beside it, so an
upload or a live analyst call never writes into the repository's own data or
audit trail. A session starts empty: nothing is loaded until the viewer asks
for it, so the screen never shows figures nobody requested.
"""

import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = Path(tempfile.gettempdir()) / "ledgerlock-web"

# A CSV is identified by the columns it carries, not by the name it arrives
# under: people rename exports. Each entry is the minimum set that has to be
# present for the file to be that ledger.
SIGNATURES = {
    "orders.csv": {"order_id", "order_amount"},
    "settlements.csv": {"settlement_id", "payment_id", "net_amount"},
    "bank_statement.csv": {"bank_txn_id", "txn_date", "narration"},
    "ground_truth.csv": {"bank_txn_id", "relation_type"},
}
REQUIRED = ("orders.csv", "settlements.csv", "bank_statement.csv")
TRUTH = "ground_truth.csv"

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
SAMPLE_SEED = 42


class DatasetError(ValueError):
    """A file the engine could not accept, phrased for the person who sent it."""


def classify(text):
    """Which ledger is this? Returns a filename or None."""
    try:
        header = next(csv.reader(io.StringIO(text)))
    except StopIteration:
        return None
    cols = {h.strip() for h in header}
    # Ground truth is checked first: it shares bank_txn_id with the statement,
    # and mistaking the answer key for a ledger would be a silent disaster.
    for name in (TRUTH, *REQUIRED):
        if SIGNATURES[name] <= cols:
            return name
    return None


@dataclass
class FileInfo:
    name: str
    rows: int
    columns: list


class Session:
    """One dataset under examination, plus the run directory that belongs to it."""

    def __init__(self, session_id=None, label="none"):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.label = label
        self.root = WORKSPACE / self.id
        self.data_dir = self.root / "data"
        self.runs_dir = self.root / "runs"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.seed = None

    # -- mutation ----------------------------------------------------------

    def _adopt_recorded_analyst(self):
        """Copy the recorded model responses so replay works with no key."""
        for name in ("analyst_cache.jsonl", "analyst_provider.json"):
            src = ROOT / "runs" / name
            if src.exists():
                shutil.copy(src, self.runs_dir / name)

    def generate(self, seed):
        """Run the shipped generator into this session's data directory."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "data" / "generate.py"),
             "--seed", str(seed), "--out", str(self.data_dir)],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        if proc.returncode != 0:
            raise DatasetError(f"the generator failed: {proc.stderr.strip()[:400]}")
        self.seed = seed
        self.label = f"seed {seed}"
        if seed == SAMPLE_SEED:
            self._adopt_recorded_analyst()
        return self.describe()

    def ingest(self, uploads):
        """uploads: [(filename, bytes)]. Classified by header, never by name.

        A new bank statement without a new answer key discards the old key:
        ground truth is keyed by bank_txn_id, and those ids repeat across
        datasets, so a stale key would grade the wrong data and look valid.
        """
        accepted, rejected = [], []
        for name, blob in uploads:
            if len(blob) > MAX_UPLOAD_BYTES:
                rejected.append({"name": name, "why": "larger than 16 MB"})
                continue
            try:
                text = blob.decode("utf-8-sig")
            except UnicodeDecodeError:
                rejected.append({"name": name, "why": "not UTF-8 text"})
                continue
            kind = classify(text)
            if kind is None:
                rejected.append({"name": name,
                                 "why": "header matches no known ledger"})
                continue
            (self.data_dir / kind).write_text(text, encoding="utf-8")
            accepted.append({"name": name, "recognised_as": kind})

        kinds = {a["recognised_as"] for a in accepted}
        dropped = None
        if "bank_statement.csv" in kinds and TRUTH not in kinds:
            truth = self.data_dir / TRUTH
            if truth.exists():
                truth.unlink()
                dropped = TRUTH

        if accepted:
            self.label = "uploaded"
            self.seed = None
            self._adopt_recorded_analyst()
        return {"accepted": accepted, "rejected": rejected,
                "dropped": dropped, "dataset": self.describe()}

    # -- inspection --------------------------------------------------------

    def _info(self, name):
        path = self.data_dir / name
        if not path.exists():
            return None
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            rows = sum(1 for _ in reader)
        return FileInfo(name, rows, header)

    def describe(self):
        files = {n: self._info(n) for n in (*REQUIRED, TRUTH)}
        missing = [n for n in REQUIRED if files[n] is None]
        return {
            "session_id": self.id,
            "label": self.label,
            "seed": self.seed,
            "ready": not missing,
            "missing": missing,
            "has_answer_key": files[TRUTH] is not None,
            "files": [{"name": f.name, "rows": f.rows, "columns": f.columns}
                      for f in files.values() if f],
        }

    def rows(self, name, limit=None):
        path = self.data_dir / name
        if not path.exists():
            raise DatasetError(f"{name} is not in this dataset")
        with path.open(newline="", encoding="utf-8") as fh:
            out = list(csv.DictReader(fh))
        return out[:limit] if limit else out


class SessionStore:
    """In-memory registry. The viewer is a single-user local tool; there is no
    database here on purpose (project-statement.md §16)."""

    def __init__(self):
        self._sessions = {}
        self._current = None

    def current(self):
        """The session this browser is working in. Empty until asked to fill."""
        if self._current is None or self._current.id not in self._sessions:
            self._current = self.new()
        return self._current

    def new(self):
        s = Session()
        self._sessions[s.id] = s
        return s

    def get(self, session_id):
        s = self._sessions.get(session_id)
        if s is None:
            raise DatasetError("that session has expired; reload the page")
        return s

    @staticmethod
    def clear_workspace():
        shutil.rmtree(WORKSPACE, ignore_errors=True)
