# Balloon

> Claude writes the 3D model. Claude writes the dialogue. ElevenLabs gives it a voice. You meet it in VR.

This file orients Claude Code when working in this repo. Read it first; refer back when scope, style, or architecture questions come up.

---

## Project context

**Balloon** is an agentic NPC generator. The user describes a character ("grumpy dwarf blacksmith named Bjorn"); a Claude agent writes a Blender Python script that builds the body, calls ElevenLabs to voice the dialogue lines, and the result drops into a WebXR scene the user can walk into.

The pitch hits all the hackathon buzzwords: **agent orchestration (Anthropic) + procedural 3D (Blender) + voice (ElevenLabs) + immersive (WebXR)**. The story is "one prompt → a 3D character standing in front of you in VR, talking."

**Hackathon:** DesignXR Hackathon. Submission via Devpost. Deadline date — TBD, confirm with user. ElevenLabs is a listed sponsor (Pro tier in the prize stack), so using their API directly is on-pitch and not an arbitrary dependency.

**Judging criteria (from the README — these are the actual weights):**
- Innovation & Creativity — **25%**
- Design & User Experience — **25%**
- Technical Implementation — **20%**
- Impact & Use Case Relevance — **20%**
- Presentation & Demo — **10%**

Innovation + Design + Presentation = 60% of the score. **Polish and the "wow, Claude wrote a 3D character on the fly" moment matter more than feature breadth.** Technical Implementation is only 20% — don't over-engineer; do over-demo.

**Constraints that shape every decision:**
- Solo developer, hackathon timeframe — scope discipline beats feature count
- Demo video: **2–4 minutes, mandatory**. Missing or incomplete = disqualified. Every feature must be demoable on camera in that window.
- Submission also needs: Problem Statement & Solution, Screenshots, Tech Stack, Platform/Device Details. Keep notes during the build so this isn't a last-day scramble.
- Backend runs **locally on the dev's M4** during the demo, not a public deploy. Blender-headless on Apple Silicon is the assumption; cloud Blender is out of scope.
- No React, no Xcode/native iOS, no bundler — vanilla JS + A-Frame on the frontend, Python + FastAPI on the backend.
- The agent loop is the differentiator. Mocked dialogue and stubbed Blender don't count — the demo must show Claude actually writing the script and the lines.

---

## Architecture

```
User prompt
  │
  ▼
Frontend (A-Frame + vanilla JS)
  │  POST /api/generate
  ▼
Backend (FastAPI, runs on M4)
  │
  ▼
Claude agent loop (anthropic SDK, tool use)
  ├── Tool: build_3d_model(name, bpy_script)
  │     └── writes script → blender --background --python → /models/{name}.glb
  └── Tool: synthesize_voice_lines(name, lines, voice_id)
        └── ElevenLabs API → /models/{name}_{line_id}.mp3
  │
  ▼
Backend returns { glb_url, audio: [{line_id, url, text}] }
  │
  ▼
Frontend loads .glb into <a-entity>, attaches spatial audio,
positions in front of camera, plays greeting
```

**Single agent, two tools, one round of tool use minimum.** Claude can iterate (regenerate script if Blender errors) but the happy path is: receive traits → call build_3d_model → call synthesize_voice_lines → return.

---

## Repo layout

```
.
├── index.html          # Single-page A-Frame frontend (JS inline)
├── server/
│   ├── main.py         # FastAPI routes + static serving + .env loader
│   └── agent.py        # Claude agent loop, both tools, ElevenLabs client
├── models/             # Generated .glb / .mp3 (gitignored)
├── assets/media/       # Logo and demo media
├── .env.example        # Template — copy to .env and fill in keys + voice IDs
└── CLAUDE.md           # This file
```

JS is inline in `index.html` (~90 lines). When that file approaches ~250 lines, split into `src/main.js` etc. Same for the server: `agent.py` currently combines the agent loop, Blender executor, and ElevenLabs client; split into `blender.py` / `voice.py` if any of them grow past their fair share.

---

## What's wired right now

- `index.html` — text input + Generate button → `POST /api/generate` → loads returned `.glb` into the `<a-entity id="npc">` via `gltf-model`, attaches positional audio for the greeting, exposes other lines as click-to-play buttons.
- `server/main.py` — FastAPI app. `POST /api/generate` calls into `agent.generate_npc()`. Mounts `/models/` and `/` as static so the whole app runs single-origin on port 8000.
- `server/agent.py` — full agent loop. `claude-opus-4-7`, adaptive thinking, `effort: high`. Two `@beta_tool` functions (`build_3d_model`, `synthesize_voice_lines`). System prompt encodes the bpy constraint sheet. Module-level `_results` dict is the side channel for extracting tool outputs at the end of the loop (single-threaded only — switch to `contextvars` for concurrent requests).
- `.env.example` + tiny inline `.env` loader at the top of `agent.py` — no `python-dotenv` dependency.

---

## Code style

**Frontend (JS):**
- Vanilla ES modules. No React, no bundler, no TypeScript.
- A-Frame for the scene; don't reach for raw Three.js unless A-Frame can't do it.
- Short, focused files. ~250 lines is a split signal.
- No abbreviations in new code: `camera` not `cam`, `renderer` not `ren`.

**Backend (Python):**
- Python 3.11+. FastAPI + `anthropic` SDK + `requests` (or `httpx`) for ElevenLabs.
- One module per concern (see repo layout). Don't put the agent loop in `main.py`.
- Type hints on public functions. No need for full mypy strictness.
- Secrets via environment variables (`ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`). Never read keys from the request body.

**Both:**
- Comments explain *why*, not *what*.
- Constants at the top of each module with a comment if the unit/range isn't obvious.
- No premature abstraction. It's a hackathon — don't build a plugin system.

---

## Architecture principles

1. **The agent is the source of truth.** Don't pre-script dialogue or pre-bake models. If the demo works without Claude actually being called, the demo is missing the point.
2. **The backend owns the secrets.** API keys never reach the browser. The frontend talks to one endpoint and gets URLs back.
3. **Generated assets are files on disk.** Write `.glb` and `.mp3` to `/models`, serve as static. No streaming, no in-memory blobs.
4. **bpy scripts are constrained.** Claude is told exactly which primitives it can use, the scale, the orientation, and where to export. The constraint sheet is in the system prompt — see "AI / Agent specifics" below.
5. **Fail visibly during dev, gracefully in demo.** Backend logs the bpy script and Blender's stderr on failure. Frontend shows "couldn't generate that one, try again" rather than crashing the scene.
6. **One scene, mutate in place.** When a new NPC is generated, replace the existing `<a-entity>` content; don't reload the page or rebuild the scene.

---

## Hackathon-critical features (build order)

Build in this order. Don't start the next one until the previous demos cleanly.

1. **Consolidate to one frontend.** Pick `index.html` as the entry point; absorb the prompt-input UI from `src/main.js`. Delete or archive the dialogue-generator-only flow.
2. **Stand up the real agent loop in `server/agent.py`.** Two tool definitions, real Anthropic SDK calls, returns the structured result. Test with a fake Blender (write a stub `.glb` path) so we know the orchestration works before depending on Blender.
3. **Wire Blender headless on M4.** Run Claude's emitted bpy script through `blender --background --python`. Iterate on the system prompt until the output is recognizable as the prompted character.
4. **Wire ElevenLabs.** Lines come from Claude (in the same agent turn), audio comes back from ElevenLabs. Cache by `(voice_id, text)` so re-runs don't re-bill.
5. **Frontend ↔ backend integration.** Replace the alert with a real fetch. Load the returned `.glb` into the scene; play the greeting audio.
6. **Spatial audio in A-Frame.** Attach the audio source to the NPC entity, set `positional: true`. The voice should come *from the character*, not the speakers.
7. **Polish + demo video.** Loading state, lighting, fallback messaging, recording.

**Stretch (only if 1–7 are solid 48h before deadline):**
- Multiple NPCs in one scene
- Conversation back to the NPC (Web Speech recognition → Claude → reply line synth)
- Animated idle (simple `a-animation` rotation/bob)
- "Regenerate" button that asks Claude to redo only the model or only the voice

**Cut entirely:**
- React, TypeScript, build tooling
- Native iOS / Xcode / Vision Pro Swift
- Cloud Blender, model marketplaces, mesh-quality 3D APIs (Meshy/Tripo)
- User accounts, persistence, history of past NPCs across sessions
- Photorealistic materials, PBR, lighting setups beyond ambient + one directional

---

## AI / Agent specifics

The agent runs in `server/agent.py`. One Anthropic SDK call with tool use, looped until Claude stops calling tools.

**Tool: `build_3d_model`**
- Input: `name: str`, `bpy_script: str`
- Behavior: writes script to `/tmp/{name}.py`, runs `blender --background --python /tmp/{name}.py`, returns `{path: "/models/{name}.glb", error: str | null}`
- Constraints baked into the system prompt:
  - Use only `bpy.ops.mesh.primitive_*` (cube, uv_sphere, cylinder, cone, torus)
  - Total height ~2m, Y-up, origin at the feet
  - Apply colored materials via `bpy.data.materials.new` + `principled_bsdf.inputs["Base Color"]`
  - Export to `/tmp/{name}.glb` via `bpy.ops.export_scene.gltf(filepath=..., export_format='GLB')`
  - No external imports, no file I/O outside `/tmp`, no network calls

**Tool: `synthesize_voice_lines`**
- Input: `name: str`, `voice_id: str`, `lines: list[{id: str, text: str}]`
- Behavior: calls ElevenLabs TTS for each line, writes `.mp3` to `/models/{name}_{id}.mp3`, returns array of `{id, url, text}`
- The agent picks `voice_id` from a small curated list (gravelly, cheerful, mysterious, etc.) based on the character traits.

**System prompt** establishes the agent as a procedural NPC builder, gives it the constraint sheet above, and tells it to produce **at minimum** a greeting, an idle line, and a farewell. It is allowed to retry the bpy script once if Blender errors.

**Latency target:** under 30s end-to-end for the first NPC. Blender is the bottleneck (~5–15s on M4); voice synth is ~2s per line in parallel.

**Failure mode:** if Blender fails twice, return a fallback `default.glb` (a low-poly humanoid kept in `/models/`) with the agent-written voice lines still attached. The demo never dies on stage.

---

## Demo video constraints

Every feature must answer: *can I show this in 30 seconds of video?* If not, cut it or hide it.

- **2–4 minutes, mandatory.** Missing video = disqualified.
- Open with the prompt being typed/spoken — establish the "one prompt in" promise immediately.
- The agent loop must be visible: show the bpy script being written, show Blender producing the file, show the character appearing. This is what sells Innovation + Technical to the judges.
- The voice arriving from the character in 3D space is the centerpiece — give it clean airtime (~30–45s).
- Close with VR headset footage if available, mobile WebXR otherwise.
- Keep an off-camera plan for the failure case (cached fallback NPC) so a live re-run on demo day doesn't blow up.

---

## When in doubt

- **Scope tension?** Cut features, not polish.
- **Refactor vs. ship?** Refactor once at the start (consolidate the two frontends), then ship.
- **New dependency?** Default no. The stack is A-Frame, FastAPI, anthropic SDK, ElevenLabs, Blender. Adding more is a flag for scope creep.
- **Naming?** Match this file's vocabulary: NPC, agent, tool, scene, prompt, voice line, model.
- **Stuck on a bug for >30 min?** Stop, write down what you tried, ask for help with that context.
