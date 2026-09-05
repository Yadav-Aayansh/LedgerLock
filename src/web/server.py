"""HTTP front door for the viewer.

Thin on purpose. Every route does three things: resolve a session, call
`pipeline.execute` or a `views` function, return the result. No matching
logic, no arithmetic, no scoring lives here — if it did, the screen could
disagree with `results.md`, and then neither could be trusted.

Binds 127.0.0.1. There is no auth because there is no remote surface, and
adding one would be building the product §16 says not to build.
"""

import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pipeline                                            # noqa: E402
from analyst.client import PRESETS, PROVIDER_INFO          # noqa: E402
from matcher import records                                # noqa: E402
from web import views                                      # noqa: E402
from web.session import DatasetError, SessionStore         # noqa: E402

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="LedgerLock", docs_url="/api/docs", redoc_url=None)
store = SessionStore()

# The last run per session, so /score and the drill-downs do not re-run the
# engine and risk showing figures from a different pass.
_last_run = {}


# -- request bodies ---------------------------------------------------------

class GenerateRequest(BaseModel):
    seed: int = Field(default=42, ge=0, le=2**31 - 1)


class AnalystRequest(BaseModel):
    """How to reach a model. `api_key` is used for this request and dropped."""
    mode: str = Field(default="off", pattern="^(off|replay|live)$")
    provider: str = "local"
    base_url: str | None = None
    model: str | None = None
    protocol: str | None = Field(default=None, pattern="^(anthropic|openai)$")
    api_key: str | None = None


class RunRequest(BaseModel):
    analyst: AnalystRequest | None = None


# -- helpers ----------------------------------------------------------------

def _session(session_id):
    try:
        return store.get(session_id)
    except DatasetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _require_ready(session):
    d = session.describe()
    if not d["ready"]:
        raise HTTPException(
            status_code=400,
            detail=f"still missing {', '.join(d['missing'])}")
    return d


def _provider(session, cfg):
    if cfg is None:
        return pipeline.resolve_provider(session.runs_dir), "off"
    if cfg.provider not in PRESETS:
        raise HTTPException(status_code=400,
                            detail=f"unknown provider '{cfg.provider}'")
    if cfg.base_url and not cfg.base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail="base URL must start with http:// or https://")
    provider = pipeline.resolve_provider(
        session.runs_dir,
        preset=cfg.provider,
        base_url=cfg.base_url or None,
        model=cfg.model or None,
        protocol=cfg.protocol,
        key_value=cfg.api_key or None,
    )
    return provider, cfg.mode


# -- routes -----------------------------------------------------------------

@app.get("/api/providers")
def providers():
    """Presets plus suggested model ids, so the connection form can prefill."""
    out = {}
    for name, (url, proto, key, model) in PRESETS.items():
        label, models = PROVIDER_INFO.get(name, (name, []))
        out[name] = {"label": label, "base_url": url, "protocol": proto,
                     "key_env": key, "default_model": model or (models[0] if models else ""),
                     "models": models}
    return {"presets": out}


@app.get("/api/session")
def session_current():
    """An empty session. Nothing loads until Generate or Upload is pressed."""
    return store.current().describe()


@app.post("/api/session/new")
def session_new():
    return store.new().describe()


@app.post("/api/session/{session_id}/generate")
def session_generate(session_id: str, body: GenerateRequest):
    session = _session(session_id)
    try:
        described = session.generate(body.seed)
    except DatasetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _last_run.pop(session_id, None)
    return described


@app.post("/api/session/{session_id}/upload")
async def session_upload(session_id: str, files: list[UploadFile] = File(...)):
    session = _session(session_id)
    uploads = [(f.filename or "unnamed", await f.read()) for f in files]
    _last_run.pop(session_id, None)
    return session.ingest(uploads)


@app.get("/api/session/{session_id}/ledgers")
def session_ledgers(session_id: str):
    """The raw lists plus the three totals that do not agree. Reads the CSVs;
    runs nothing — screen 1 is the problem, not the answer."""
    session = _session(session_id)
    _require_ready(session)
    try:
        orders, settlements, bank, groups = records.load(session.data_dir)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400,
                            detail=f"could not read this dataset: {exc}") from None
    return {
        "ledgers": views.ledgers(session),
        "disagreement": views.disagreement(orders, settlements, bank, groups),
        "batches": views.batches(groups),
    }


@app.post("/api/session/{session_id}/run")
def session_run(session_id: str, body: RunRequest | None = None):
    """Match, then optionally hand the residue to a model, then verify.

    One call because that is one pipeline: a screen that ran matching and
    analysis separately could show a state the engine never actually held.
    """
    session = _session(session_id)
    _require_ready(session)
    provider, mode = _provider(session, body.analyst if body else None)

    try:
        result = pipeline.execute(
            session.data_dir, session.runs_dir,
            seed=session.seed or 0, analyst_mode=mode, provider=provider)
    except (ValueError, KeyError) as exc:
        # A malformed upload surfaces here: say which file, not a stack trace.
        raise HTTPException(status_code=400,
                            detail=f"the engine could not read this dataset: {exc}") from None

    _last_run[session_id] = result
    return {
        "dataset": session.describe(),
        "disagreement": views.disagreement(result.orders, result.settlements,
                                          result.bank, result.groups),
        "batches": views.batches(result.groups),
        "run": views.run(result),
        "analyst": views.analyst(result),
        "redteam": views.redteam(result),
        "answer_key_available": result.scored,
    }


@app.get("/api/session/{session_id}/score")
def session_score(session_id: str):
    """The reveal. Deliberately its own call: nothing scores until asked."""
    result = _last_run.get(session_id)
    if result is None:
        raise HTTPException(status_code=409, detail="run the engine first")
    return views.score(result)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _free_port(start, tries=20):
    """The demo machine may already be using the default port. Move up rather
    than dying on a stack trace, and say where it landed."""
    import socket
    for port in range(start, start + tries):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"no free port between {start} and {start + tries}")


def main():
    import os

    import uvicorn
    port = _free_port(int(os.environ.get("LEDGERLOCK_PORT", "8000")))
    print(f"\n  LedgerLock viewer  ->  http://127.0.0.1:{port}\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
