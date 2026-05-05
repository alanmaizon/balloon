"""FastAPI server for NPC Chatter.

Serves the A-Frame frontend, the generated /models/ assets, and the agent API.
Single-origin: open http://localhost:8000 — no CORS, no separate dev server.
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent

ROOT = Path(__file__).parent.parent

app = FastAPI(title="NPC Chatter")


class GenerateRequest(BaseModel):
    prompt: str


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Run the agent loop end-to-end. Returns {glb_url, audio: [{id, url, text}]}."""
    try:
        return agent.generate_npc(req.prompt)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    return {"ok": True}


# Mount order matters — explicit routes above are matched first, then mounts in
# registration order. Generated assets live under /models; everything else
# (index.html, src/, etc.) is served from the project root.
app.mount("/models", StaticFiles(directory=str(ROOT / "models")), name="models")
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")
