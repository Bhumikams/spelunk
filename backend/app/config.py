"""Central configuration, read from environment variables (.env supported)."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    # --- LLM ---------------------------------------------------------------
    llm_provider: str = os.getenv("LLM_PROVIDER", "groq").lower()
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    groq_reasoning_effort: str = os.getenv("GROQ_REASONING_EFFORT", "low")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))

    # --- Embeddings --------------------------------------------------------
    embed_model: str = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    embed_dim: int = _int("EMBED_DIM", 384)
    embed_cache_dir: str | None = os.getenv("EMBED_CACHE_DIR") or None
    embed_batch: int = _int("EMBED_BATCH", 128)

    # --- Storage -----------------------------------------------------------
    # "atlas" (MongoDB Atlas Search + Vector Search) or "memory" (tests / offline demo)
    store: str = os.getenv("STORE", "atlas").lower()
    mongodb_uri: str = os.getenv("MONGODB_URI", "")
    mongodb_db: str = os.getenv("MONGODB_DB", "spelunk")
    vector_index_name: str = os.getenv("VECTOR_INDEX_NAME", "vector_index")
    text_index_name: str = os.getenv("TEXT_INDEX_NAME", "text_index")

    # --- Indexing ----------------------------------------------------------
    default_repo: str = os.getenv("DEFAULT_REPO", "https://github.com/morluto/rea")
    max_file_kb: int = _int("MAX_FILE_KB", 320)
    max_files: int = _int("MAX_FILES", 6000)
    index_tests: bool = _bool("INDEX_TESTS", False)
    extra_exclude: list[str] = field(default_factory=lambda: _list("EXTRA_EXCLUDE"))
    chunk_max_lines: int = _int("CHUNK_MAX_LINES", 90)
    chunk_min_lines: int = _int("CHUNK_MIN_LINES", 8)

    # --- Retrieval ---------------------------------------------------------
    top_k: int = _int("TOP_K", 6)
    candidates: int = _int("CANDIDATES", 40)  # per retriever, before fusion
    rrf_k: int = _int("RRF_K", 60)
    context_chars: int = _int("CONTEXT_CHARS", 1800)  # per source sent to the LLM

    # --- API ---------------------------------------------------------------
    cors_origins: list[str] = field(
        default_factory=lambda: _list(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
    )
    # Public demo mode: visitors can search and ask, but cannot index or delete repos
    read_only: bool = _bool("READ_ONLY", False)


settings = Settings()
