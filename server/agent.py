"""
Balloon agent loop.

Single Anthropic SDK call with tool use. Claude receives a character
description, calls two tools (build_3d_model, synthesize_voice_lines), and the
orchestrator returns URLs for the resulting .glb and .mp3 files.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import anthropic
import httpx
from anthropic import beta_tool

MODEL = "claude-opus-4-7"
ROOT = Path(__file__).parent.parent
MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)


def _load_env_file(path: Path) -> None:
    """Minimal .env loader. Sets os.environ for any KEY=VALUE not already set."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file(ROOT / ".env")

# Voice palette. Names are fixed (Claude sees them in the system prompt);
# IDs come from .env so they're configurable per-deployment.
VOICES = {
    "gravelly_old":    os.environ.get("VOICE_GRAVELLY_OLD", ""),
    "cheerful_young":  os.environ.get("VOICE_CHEERFUL_YOUNG", ""),
    "mysterious_low":  os.environ.get("VOICE_MYSTERIOUS_LOW", ""),
    "warrior_strong":  os.environ.get("VOICE_WARRIOR_STRONG", ""),
    "merchant_smooth": os.environ.get("VOICE_MERCHANT_SMOOTH", ""),
}

SYSTEM_PROMPT = """You are a procedural NPC builder. Given a character description from the user, do this:

1. Call `build_3d_model` with a unique snake_case `name` (format strictly as `<species>_<class>_<trait>`, e.g., `goblin_merchant_greedy`) and a `bpy_script` that constructs the character.
2. Call `synthesize_voice_lines` with the same `name`, a `voice_id` from the list below, and 3-5 dialogue lines.

Both tools must succeed. Return a one-sentence summary at the end.

## bpy script constraints (MUST follow)

- Use only `bpy.ops.mesh.primitive_*` (cube, uv_sphere, cylinder, cone, torus).
- Total height ~2 meters; the character's feet at y=0 (Y-up).
- Apply colored materials via `bpy.data.materials.new(...)` and set `principled_bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)`.
- Export with `bpy.ops.export_scene.gltf(filepath=f"/tmp/{{name}}.glb", export_format="GLB")`.
- No imports beyond `bpy`. No file I/O outside `/tmp`. No network calls.
- Start the script with `import bpy` and clear the default scene before adding geometry.

## Voice palette

{voice_list}

## Dialogue

Provide at least: a greeting, an idle line, and a farewell. Write them in the character's voice.
Each line is a dict: {{"id": "<short_snake_case>", "text": "<spoken text>"}}.

If `build_3d_model` returns an "ERROR:" string, fix the script and retry the tool ONCE.
"""

# Per-request side channel. Tools write here; generate_npc reads at the end.
# Single-threaded server only — for concurrent requests use contextvars.ContextVar.
_results: dict = {}


def _next_index() -> int:
    """Highest existing NN- prefix in /models, plus one. 1 if none exist."""
    indices = [
        int(m.group(1))
        for f in MODELS_DIR.glob("*")
        if (m := re.match(r"^(\d+)-", f.name)) and f.is_dir()
    ]
    return max(indices) + 1 if indices else 1


@beta_tool
def build_3d_model(name: str, bpy_script: str) -> str:
    """Run a bpy script in headless Blender, producing /models/{name}.glb.

    Args:
        name: snake_case identifier — drives the output filename.
        bpy_script: Python source using the bpy API. Must export to /tmp/{name}.glb.

    Returns:
        Confirmation string on success, or an "ERROR: ..." string Claude can retry on.
    """
    prefixed_dir = f"{_results['index']:02d}-model"
    out_dir = MODELS_DIR / prefixed_dir
    out_dir.mkdir(exist_ok=True)
    
    script_path = Path(f"/tmp/{name}_build.py")
    tmp_glb = Path(f"/tmp/{name}.glb")
    script_path.write_text(bpy_script)

    result = subprocess.run(
        ["blender", "--background", "--python", str(script_path)],
        capture_output=True, text=True, timeout=60,
    )

    if not tmp_glb.exists():
        return (
            f"ERROR: Blender did not produce {tmp_glb}. "
            f"stderr (last 500 chars):\n{result.stderr[-500:]}"
        )

    final = out_dir / f"{name}.glb"
    tmp_glb.replace(final)
    _results["name"] = name
    _results["glb_url"] = f"models/{prefixed_dir}/{name}.glb"
    _results["dir_name"] = prefixed_dir
    return f"OK: built models/{prefixed_dir}/{name}.glb"


@beta_tool
def synthesize_voice_lines(name: str, voice_id: str, lines: list[dict]) -> str:
    """Generate audio for each line via ElevenLabs, save as /models/{name}_{id}.mp3.

    Args:
        name: snake_case identifier — drives output filenames.
        voice_id: ElevenLabs voice ID (from the voice palette).
        lines: List of {"id": "...", "text": "..."} dicts. 3-5 entries.

    Returns:
        Confirmation string on success, "ERROR: ..." on failure.
    """
    prefixed_dir = f"{_results['index']:02d}-model"
    out_dir = MODELS_DIR / prefixed_dir
    out_dir.mkdir(exist_ok=True)
    
    audio = []
    for line in lines:
        try:
            mp3_bytes = _elevenlabs_tts(voice_id, line["text"])
        except Exception as e:
            return f"ERROR: voice synthesis failed for line {line['id']}: {e}"
        out = out_dir / f"{name}_{line['id']}.mp3"
        out.write_bytes(mp3_bytes)
        audio.append({
            "id": line["id"],
            "text": line["text"],
            "url": f"models/{prefixed_dir}/{name}_{line['id']}.mp3",
        })
    _results["audio"] = audio
    return f"OK: synthesized {len(audio)} lines."


def _elevenlabs_tts(voice_id: str, text: str) -> bytes:
    """POST to ElevenLabs TTS, return mp3 bytes. Raises on HTTP error."""
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
            "accept": "audio/mpeg",
        },
        json={"text": text, "model_id": "eleven_turbo_v2_5"},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.content


def generate_npc(user_prompt: str) -> dict:
    """Run the agent loop. Returns {"glb_url": str, "audio": [{id, url, text}]}."""
    _results.clear()
    _results["index"] = _next_index()
    client = anthropic.Anthropic()
    voice_list = "\n".join(f"  - {nm}: {vid}" for nm, vid in VOICES.items())

    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=SYSTEM_PROMPT.format(voice_list=voice_list),
        tools=[build_3d_model, synthesize_voice_lines],
        messages=[{"role": "user", "content": user_prompt}],
    )

    for _message in runner:
        pass

    if "glb_url" not in _results or "audio" not in _results:
        raise RuntimeError(
            f"Agent finished without producing both outputs. Got keys: {list(_results)}"
        )
    payload = {"glb_url": _results["glb_url"], "audio": _results["audio"]}
    # Persist alongside the assets so the frontend can deep-link via ?npc=<name>.
    out_dir = MODELS_DIR / _results["dir_name"]
    out_dir.mkdir(exist_ok=True)
    json_path = out_dir / f"{_results['name']}.json"
    json_path.write_text(json.dumps(payload, indent=2))
    
    # We return the frontend-friendly identifier so it can update the URL
    payload["npc_id"] = f"{_results['dir_name']}/{_results['name']}"
    return payload


if __name__ == "__main__":
    result = generate_npc("Bjorn — a grumpy dwarf blacksmith with a long red beard")
    print(json.dumps(result, indent=2))
