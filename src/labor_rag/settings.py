import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
  dataDir: Path
  provider: str = "extractive"
  apiKey: str = field(default="", repr=False)
  chatModel: str = ""
  embeddingModel: str = "text-embedding-3-small"
  retrievalMode: str = "bm25"
  rerankerPath: str = ""
  maxContextChars: int = 8000
  minSimilarity: float = 0.35
  timeout: float = 45


def loadSettings(projectPath, environ=None):
  projectPath = Path(projectPath)
  values = {**dotenv_values(projectPath / ".env"), **(os.environ if environ is None else environ)}
  def value(name, default=""):
    return str(values.get(name) or default).strip()

  provider = value("RAG_PROVIDER", "extractive")
  retrievalMode = value("RAG_RETRIEVAL", "bm25")
  if provider not in ("extractive", "openai") or retrievalMode not in ("bm25", "hybrid"):
    raise ValueError("RAG_PROVIDER 或 RAG_RETRIEVAL 設定無效")
  apiKey, chatModel = value("OPENAI_API_KEY"), value("OPENAI_CHAT_MODEL")
  if (provider == "openai" or retrievalMode == "hybrid") and not apiKey:
    raise ValueError("請在 .env 設定 OPENAI_API_KEY")
  if provider == "openai" and not chatModel:
    raise ValueError("請在 .env 設定 OPENAI_CHAT_MODEL")
  try:
    maxContextChars = int(value("RAG_MAX_CONTEXT_CHARS", "8000"))
    minSimilarity = float(value("RAG_MIN_SIMILARITY", "0.35"))
    timeout = float(value("OPENAI_TIMEOUT_SECONDS", "45"))
    if not 100 <= maxContextChars <= 100000 or not -1 <= minSimilarity <= 1 or not math.isfinite(timeout) or not 1 <= timeout <= 120:
      raise ValueError()
  except ValueError:
    raise ValueError("內容預算、相似度門檻或逾時設定無效") from None
  dataDir = Path(value("RAG_DATA_DIR", "data/index"))
  if not dataDir.is_absolute():
    dataDir = projectPath / dataDir
  return Settings(dataDir, provider, apiKey, chatModel, value("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
    retrievalMode, value("RAG_RERANKER_PATH"), maxContextChars, minSimilarity, timeout)
