"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
# These defaults represent the 6-model debate methodology.
COUNCIL_MODELS = [
    "openai/gpt-5.2",
    "openai/codex-5.3",
    "anthropic/claude-opus-4.6",
    "anthropic/claude-sonnet-4.6",
    "google/gemini-3-pro",
    "x-ai/grok-4",
]

# Coordinator model - used only as a fallback to force consensus
EXECUTION_COORDINATOR_MODEL = "openai/gpt-5.2"

# Maximum number of debate rounds before coordinator fallback
CONSENSUS_MAX_ROUNDS = 4

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"
