"""Debate-to-consensus LLM Council orchestration."""

import asyncio
import json
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .config import CONSENSUS_MAX_ROUNDS, COUNCIL_MODELS, EXECUTION_COORDINATOR_MODEL
from .openrouter import query_model, query_models_parallel


def _extract_json_blob(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of a JSON object from model text."""
    if not text:
        return None

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None

    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None

    return None


def _normalize_model_name(name: str) -> Optional[str]:
    """Map free-form model names back to configured OpenRouter IDs."""
    if not name:
        return None

    name_lc = name.lower().strip()
    council_lc = {model.lower(): model for model in COUNCIL_MODELS}
    if name_lc in council_lc:
        return council_lc[name_lc]

    for model in COUNCIL_MODELS:
        short_name = model.split("/")[-1].lower()
        if name_lc == short_name or name_lc in short_name or short_name in name_lc:
            return model

    return None


def _parse_debate_response(model: str, raw_text: str) -> Dict[str, Any]:
    """Parse per-model debate output with safe fallbacks."""
    parsed = _extract_json_blob(raw_text) or {}
    consensus_status = str(parsed.get("consensus_status", "")).upper().strip()
    if consensus_status not in {"AGREED", "NOT_AGREED"}:
        consensus_status = "NOT_AGREED"

    assignments = []
    for item in parsed.get("task_assignments", []) if isinstance(parsed.get("task_assignments"), list) else []:
        if not isinstance(item, dict):
            continue
        normalized_model = _normalize_model_name(str(item.get("model", "")).strip())
        responsibility = str(item.get("responsibility", "")).strip()
        if normalized_model and responsibility:
            assignments.append({
                "model": normalized_model,
                "responsibility": responsibility,
            })

    return {
        "model": model,
        "debate": raw_text or "",
        "analysis": str(parsed.get("analysis", "")).strip(),
        "consensus_status": consensus_status,
        "consensus_plan": str(parsed.get("consensus_plan", "")).strip(),
        "task_assignments": assignments,
        "execution_notes": str(parsed.get("execution_notes", "")).strip(),
    }


def _merge_assignments(stage2_results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Merge proposed task assignments across models."""
    merged: Dict[str, set] = defaultdict(set)
    for result in stage2_results:
        for assignment in result.get("task_assignments", []):
            model = assignment.get("model")
            responsibility = assignment.get("responsibility")
            if model and responsibility:
                merged[model].add(responsibility)

    final_assignments = []
    for model in COUNCIL_MODELS:
        responsibilities = sorted(merged.get(model, set()))
        if responsibilities:
            final_assignments.append({
                "model": model,
                "responsibility": "; ".join(responsibilities),
            })
    return final_assignments


def _derive_consensus(stage2_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Determine whether the round has reached consensus."""
    valid_results = [r for r in stage2_results if r.get("debate")]
    if not valid_results:
        return {
            "consensus_reached": False,
            "consensus_plan": "",
            "task_assignments": [],
        }

    all_agreed = all(r.get("consensus_status") == "AGREED" for r in valid_results)
    plans = [r.get("consensus_plan", "").strip() for r in valid_results if r.get("consensus_plan", "").strip()]

    if not plans:
        return {
            "consensus_reached": False,
            "consensus_plan": "",
            "task_assignments": [],
        }

    top_plan, _ = Counter(plans).most_common(1)[0]
    assignments = _merge_assignments(valid_results)

    return {
        "consensus_reached": all_agreed and bool(assignments),
        "consensus_plan": top_plan,
        "task_assignments": assignments,
    }


def _format_stage1_context(stage1_results: List[Dict[str, Any]]) -> str:
    return "\n\n".join(
        f"Model: {result['model']}\nIndependent plan:\n{result['response']}"
        for result in stage1_results
    )


def _format_round_context(round_history: List[Dict[str, Any]]) -> str:
    if not round_history:
        return "No prior debate rounds."

    chunks = []
    for round_data in round_history:
        round_entries = []
        for entry in round_data["entries"]:
            round_entries.append(
                f"- {entry['model']} | status={entry['consensus_status']}\n"
                f"  plan: {entry.get('consensus_plan', '')}"
            )
        chunks.append(f"Round {round_data['round']}:\n" + "\n".join(round_entries))
    return "\n\n".join(chunks)


def _build_stage2_prompt(
    model: str,
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    round_number: int,
    round_history: List[Dict[str, Any]],
) -> str:
    available_models = "\n".join(f"- {m}" for m in COUNCIL_MODELS)
    return f"""You are participating in a multi-LLM council debate to solve a user problem.

Your model identity: {model}
User problem:
{user_query}

Independent plans from all models (Stage 1):
{_format_stage1_context(stage1_results)}

Debate history so far:
{_format_round_context(round_history)}

Current round: {round_number}
Available models for assignment:
{available_models}

Instructions:
1. Critique and refine the combined approach.
2. Propose the definitive best consensus plan.
3. Provide explicit task allocation across models.
4. Set consensus_status to AGREED only if you can support this as the final shared plan right now.
5. Return ONLY valid JSON (no markdown fences) with this exact schema:
{{
  "analysis": "short but concrete debate reasoning",
  "consensus_status": "AGREED or NOT_AGREED",
  "consensus_plan": "the final best shared plan as plain text",
  "task_assignments": [
    {{"model": "one model from the list above", "responsibility": "specific implementation responsibility"}}
  ],
  "execution_notes": "what must happen next to execute"
}}"""


async def stage1_collect_responses(user_query: str) -> List[Dict[str, Any]]:
    """
    Stage 1: collect independent implementation plans from all council models.
    """
    plan_prompt = (
        "You are a council member. Given the user problem below, produce your "
        "best independent implementation plan. Focus on concrete, actionable steps.\n\n"
        f"User problem:\n{user_query}"
    )
    messages = [{"role": "user", "content": plan_prompt}]
    responses = await query_models_parallel(COUNCIL_MODELS, messages)

    stage1_results: List[Dict[str, Any]] = []
    for model, response in responses.items():
        if response is not None:
            stage1_results.append({
                "model": model,
                "response": response.get("content", ""),
            })
    return stage1_results


async def _coordinator_finalize_consensus(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    round_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Force a concrete consensus plan if debate rounds fail to converge."""
    debate_dump = []
    for round_data in round_history:
        debate_dump.append(f"Round {round_data['round']}:")
        for entry in round_data["entries"]:
            debate_dump.append(
                f"Model: {entry['model']}\n"
                f"Status: {entry['consensus_status']}\n"
                f"Plan: {entry.get('consensus_plan', '')}\n"
                f"Debate: {entry.get('debate', '')}"
            )
    debate_text = "\n\n".join(debate_dump)

    prompt = f"""You are the council execution coordinator.
The council failed to reach unanimous consensus within the allowed rounds.
Produce the definitive consensus plan and task allocation to unblock execution.

User problem:
{user_query}

Stage 1 independent plans:
{_format_stage1_context(stage1_results)}

Debate transcript:
{debate_text}

Return ONLY valid JSON:
{{
  "consensus_plan": "final consolidated plan",
  "task_assignments": [
    {{"model": "model id", "responsibility": "specific responsibility"}}
  ]
}}"""
    response = await query_model(EXECUTION_COORDINATOR_MODEL, [{"role": "user", "content": prompt}])
    parsed = _extract_json_blob(response.get("content", "") if response else "") or {}

    assignments = []
    for item in parsed.get("task_assignments", []) if isinstance(parsed.get("task_assignments"), list) else []:
        if not isinstance(item, dict):
            continue
        model = _normalize_model_name(str(item.get("model", "")).strip())
        responsibility = str(item.get("responsibility", "")).strip()
        if model and responsibility:
            assignments.append({"model": model, "responsibility": responsibility})

    if not assignments:
        assignments = [
            {
                "model": model,
                "responsibility": "Contribute implementation details for the consensus plan.",
            }
            for model in COUNCIL_MODELS
        ]

    return {
        "consensus_plan": str(parsed.get("consensus_plan", "")).strip() or "Consolidated multi-model execution plan.",
        "task_assignments": assignments,
    }


async def stage2_debate_rounds(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    on_round_complete=None,
):
    """
    Stage 2: run debate rounds until consensus (or fallback coordinator decision).

    Yields round results via the on_round_complete callback for streaming, then
    returns the final (debate_entries, metadata) tuple.

    Args:
        user_query: The user's question
        stage1_results: Stage 1 results
        on_round_complete: Optional async callback(round_data) called after each round

    Returns:
        Tuple of (final round debate entries, consensus metadata)
    """
    round_history: List[Dict[str, Any]] = []
    final_round_entries: List[Dict[str, Any]] = []
    consensus_plan = ""
    task_assignments: List[Dict[str, str]] = []
    consensus_reached = False

    for round_number in range(1, CONSENSUS_MAX_ROUNDS + 1):
        prompts = [
            _build_stage2_prompt(model, user_query, stage1_results, round_number, round_history)
            for model in COUNCIL_MODELS
        ]
        tasks = [
            query_model(model, [{"role": "user", "content": prompt}])
            for model, prompt in zip(COUNCIL_MODELS, prompts)
        ]
        responses = await asyncio.gather(*tasks)

        round_entries: List[Dict[str, Any]] = []
        for model, response in zip(COUNCIL_MODELS, responses):
            raw_text = response.get("content", "") if response else ""
            round_entries.append(_parse_debate_response(model, raw_text))

        final_round_entries = round_entries
        round_data = {
            "round": round_number,
            "entries": round_entries,
        }
        round_history.append(round_data)

        round_consensus = _derive_consensus(round_entries)

        if on_round_complete:
            await on_round_complete({
                **round_data,
                "consensus_snapshot": round_consensus,
                "max_rounds": CONSENSUS_MAX_ROUNDS,
            })

        if round_consensus["consensus_reached"]:
            consensus_reached = True
            consensus_plan = round_consensus["consensus_plan"]
            task_assignments = round_consensus["task_assignments"]
            break

    fallback_used = False
    if not consensus_reached:
        fallback_used = True
        coordinator_output = await _coordinator_finalize_consensus(
            user_query=user_query,
            stage1_results=stage1_results,
            round_history=round_history,
        )
        consensus_plan = coordinator_output["consensus_plan"]
        task_assignments = coordinator_output["task_assignments"]

    metadata = {
        "rounds_completed": len(round_history),
        "consensus_reached_in_rounds": consensus_reached,
        "consensus_fallback_used": fallback_used,
        "consensus_plan": consensus_plan,
        "task_assignments": task_assignments,
        "round_history": round_history,
    }
    return final_round_entries, metadata


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Non-streaming wrapper for stage2_debate_rounds (used by non-streaming endpoint)."""
    return await stage2_debate_rounds(user_query, stage1_results)


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    consensus_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Stage 3: execute assigned work and produce an integrated final answer.
    """
    consensus_metadata = consensus_metadata or {}
    consensus_plan = consensus_metadata.get("consensus_plan", "")
    assignments = consensus_metadata.get("task_assignments", []) or []

    if not assignments:
        assignments = [
            {
                "model": model,
                "responsibility": "Contribute concrete execution details for the shared plan.",
            }
            for model in COUNCIL_MODELS
        ]

    execution_tasks = []
    for assignment in assignments:
        model = assignment["model"]
        responsibility = assignment["responsibility"]
        prompt = f"""You are executing your assigned part of a consensus plan.

User problem:
{user_query}

Consensus plan:
{consensus_plan}

Your model: {model}
Your responsibility:
{responsibility}

Provide concrete execution output for your assigned part. Include:
- exact steps
- implementation details (or code/pseudocode if applicable)
- dependencies and risk notes"""
        execution_tasks.append(
            query_model(model, [{"role": "user", "content": prompt}])
        )

    execution_raw = await asyncio.gather(*execution_tasks)
    execution_outputs = []
    for assignment, response in zip(assignments, execution_raw):
        execution_outputs.append({
            "model": assignment["model"],
            "responsibility": assignment["responsibility"],
            "output": response.get("content", "") if response else "No output returned.",
        })

    execution_text = "\n\n".join(
        f"Model: {item['model']}\nResponsibility: {item['responsibility']}\nExecution Output:\n{item['output']}"
        for item in execution_outputs
    )
    stage1_text = _format_stage1_context(stage1_results)
    stage2_text = "\n\n".join(
        f"Model: {result['model']}\nStatus: {result['consensus_status']}\nPlan: {result.get('consensus_plan', '')}"
        for result in stage2_results
    )

    final_prompt = f"""You are the execution coordinator for an LLM council.
Integrate the council's outputs into one final answer to the user.

User problem:
{user_query}

Stage 1 independent plans:
{stage1_text}

Stage 2 final debate snapshot:
{stage2_text}

Consensus plan:
{consensus_plan}

Assigned execution outputs:
{execution_text}

Produce:
1) The definitive best plan
2) A clear implementation sequence
3) Which model handles which part
4) Final actionable answer for the user"""
    final_response = await query_model(
        EXECUTION_COORDINATOR_MODEL,
        [{"role": "user", "content": final_prompt}],
    )

    if final_response is None:
        fallback = "Integrated execution summary:\n\n" + execution_text
        return {
            "model": EXECUTION_COORDINATOR_MODEL,
            "response": fallback,
            "execution_outputs": execution_outputs,
        }

    return {
        "model": EXECUTION_COORDINATOR_MODEL,
        "response": final_response.get("content", ""),
        "execution_outputs": execution_outputs,
    }


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Legacy compatibility helper kept for older code paths.
    The debate-to-consensus flow does not produce aggregate rankings.
    """
    _ = stage2_results, label_to_model
    return []


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.
    """
    title_prompt = (
        "Generate a very short title (3-5 words maximum) that summarizes the following question. "
        "The title should be concise and descriptive. Do not use quotes or punctuation in the title.\n\n"
        f"Question: {user_query}\n\nTitle:"
    )
    messages = [{"role": "user", "content": title_prompt}]

    # Try fast/cheap model first, then fall back to first council model
    for model in ["google/gemini-2.5-flash", COUNCIL_MODELS[0]]:
        response = await query_model(model, messages, timeout=30.0)
        if response and response.get("content"):
            title = response["content"].strip().strip('"\'')
            if len(title) > 50:
                title = title[:47] + "..."
            return title

    return "New Conversation"


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage debate-to-consensus council process.

    Args:
        user_query: The user's question

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    # Stage 1: Collect individual responses
    stage1_results = await stage1_collect_responses(user_query)

    # If no models responded successfully, return error
    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    # Stage 2: Debate until consensus
    stage2_results, consensus_metadata = await stage2_collect_rankings(user_query, stage1_results)

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results,
        consensus_metadata,
    )

    return stage1_results, stage2_results, stage3_result, consensus_metadata
