# CLAUDE.md - Technical Notes for LLM Council

Technical details, architectural decisions, and implementation notes for development.

## Project Overview

LLM Council is a 3-stage debate-to-consensus system where 6 LLMs independently plan, debate each other's plans, then execute assigned parts. The key innovation is multi-round debate with consensus detection and coordinator fallback.

## Architecture

### Backend (`backend/`)

**`config.py`**
- `COUNCIL_MODELS`: list of 6 OpenRouter model identifiers
- `EXECUTION_COORDINATOR_MODEL`: model that synthesizes the final answer
- `CONSENSUS_MAX_ROUNDS`: max debate rounds before coordinator fallback (default 4)
- Uses `OPENROUTER_API_KEY` from `.env`

**`openrouter.py`**
- `query_model()`: single async model query via OpenRouter API
- `query_models_parallel()`: parallel queries using `asyncio.gather()`
- Graceful degradation: returns `None` on failure, continues with successful responses
- Logs HTTP status and body excerpt on errors for debugging

**`council.py`** — Core Logic
- `stage1_collect_responses()`: parallel independent plans from all council models
- `stage2_debate_rounds()`: multi-round debate with per-round streaming callback
  - Each round: all models critique plans, propose consensus, assign tasks
  - Models return structured JSON with `consensus_status`, `consensus_plan`, `task_assignments`
  - `_derive_consensus()` checks if all models agreed and assignments exist
  - If consensus not reached in N rounds, `_coordinator_finalize_consensus()` forces a decision
- `stage2_collect_rankings()`: non-streaming wrapper around `stage2_debate_rounds()`
- `stage3_synthesize_final()`: assigned models execute their parts, coordinator integrates
- `generate_conversation_title()`: generates short title via fast model with fallback
- `run_full_council()`: runs the complete 3-stage pipeline

**`storage.py`**
- JSON-based conversation storage in `data/conversations/`
- Each conversation: `{id, created_at, title, messages[]}`
- Assistant messages: `{role, stage1, stage2, stage3}`
- Consensus metadata is NOT persisted, only returned via API

**`main.py`**
- FastAPI on port 8001, CORS for localhost:5173 and localhost:3000
- Non-streaming: `POST /api/conversations/{id}/message`
- Streaming: `POST /api/conversations/{id}/message/stream` (SSE)
  - Emits per-round `stage2_round` events via `asyncio.Queue` for live debate progress
- Startup check warns if `OPENROUTER_API_KEY` is missing

### Frontend (`frontend/src/`)

**`api.js`**
- SSE stream reader with proper chunk buffering across TCP boundaries

**`App.jsx`**
- Manages conversations and streaming state
- Handles SSE events: `stage1_start/complete`, `stage2_start/round/complete`, `stage3_start/complete`, `title_complete`, `complete`, `error`

**`components/ChatInterface.jsx`**
- Message display with stage loading indicators (shows round progress during Stage 2)
- Textarea: Enter to send, Shift+Enter for newline
- Error display for failed requests

**`components/Stage1.jsx`**
- Tabbed view of individual model implementation plans

**`components/Stage2.jsx`**
- Consensus snapshot: rounds completed, consensus reached, fallback used
- Consensus plan and task assignments display
- Expandable round-by-round debate history with per-model agreement status
- Tabbed final-round debate outputs

**`components/Stage3.jsx`**
- Coordinator's integrated final answer
- Per-model execution outputs (each model's assigned work)

## Key Design Decisions

### Debate Prompt Format
Stage 2 prompts require structured JSON output:
```json
{
  "analysis": "debate reasoning",
  "consensus_status": "AGREED or NOT_AGREED",
  "consensus_plan": "the shared plan",
  "task_assignments": [{"model": "...", "responsibility": "..."}],
  "execution_notes": "next steps"
}
```
`_parse_debate_response()` extracts JSON with fallbacks (strip markdown fences, regex extraction).

### Consensus Detection
- All models must set `consensus_status: "AGREED"`
- Valid task assignments must exist
- If not reached in `CONSENSUS_MAX_ROUNDS`, the coordinator model forces a decision

### Model Name Normalization
`_normalize_model_name()` maps free-form model names from debate responses back to configured OpenRouter IDs using substring matching. This handles cases where models refer to themselves or others by short names.

### Error Handling
- Continue with successful responses if some models fail
- Never fail the entire request due to single model failure
- Stream errors are surfaced in the UI, not silently swallowed

## Important Implementation Details

### Relative Imports
All backend modules use relative imports (`from .config import ...`). Run as `python -m backend.main` from project root.

### Port Configuration
- Backend: 8001
- Frontend: 5173 (Vite default)
- Update both `backend/main.py` and `frontend/src/api.js` if changing

### SSE Streaming Architecture
The streaming endpoint uses `asyncio.Queue` to bridge the debate callback with the async generator:
1. `stage2_debate_rounds()` runs as a background task via `asyncio.create_task()`
2. Its `on_round_complete` callback pushes round data to a queue
3. The SSE generator reads from the queue and yields events
4. A sentinel value signals debate completion

### Markdown Rendering
All ReactMarkdown components must be wrapped in `<div className="markdown-content">` for spacing. Defined globally in `index.css`.

## Data Flow

```
User Query
    ↓
Stage 1: Parallel queries → [6 independent plans]
    ↓
Stage 2: Multi-round debate → [consensus plan + task assignments]
    ↓  (per-round SSE events streamed to client)
Stage 3: Assigned execution → [per-model outputs + coordinator synthesis]
    ↓
Return: {stage1, stage2, stage3, metadata}
    ↓
Frontend: Tabbed display with round history + validation UI
```

## Common Gotchas

1. **Module Import Errors**: Always run backend as `python -m backend.main` from project root
2. **CORS Issues**: Frontend origin must match allowed origins in `main.py`
3. **JSON Parse Failures**: Models may wrap JSON in markdown fences or add extra text; `_extract_json_blob()` handles this
4. **Missing Metadata**: Consensus metadata is ephemeral (not persisted), only in API responses
5. **Missing API Key**: Backend logs a warning at startup but doesn't crash; all model queries return `None`
