from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .generation import ExtractiveGenerator
from .hybrid import HybridRetriever, LocalCrossEncoder
from .ingestion import loadChunks
from .processing import splitChunk
from .providers import OpenAIClient, OpenAIEmbedder, OpenAIGenerator
from .service import RagService
from .storage import KnowledgeStore, embeddingText
from .telemetry import TraceStore


def publicChunks(chunks):
  today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
  return [chunk for chunk in chunks if chunk.active and "public" in chunk.allowedRoles
    and (not chunk.effectiveFrom or chunk.effectiveFrom <= today)
    and (not chunk.effectiveTo or today <= chunk.effectiveTo)]


def indexMissing(store, embedder, batchSize=32):
  chunks = publicChunks(store.listChunks())
  cached = store.loadVectors(embedder.modelId, chunks)
  pending = [chunk for chunk in chunks if chunk.chunkId not in cached]
  tokenCount = 0
  for start in range(0, len(pending), batchSize):
    batch = pending[start:start + batchSize]
    vectors = embedder.embed([embeddingText(chunk) for chunk in batch])
    store.saveVectors(embedder.modelId, batch, vectors)
    tokenCount += getattr(embedder, "lastUsage", {}).get("total_tokens", 0)
  return {"indexed": len(pending), "cached": len(cached), "totalTokens": tokenCount}


@dataclass
class Runtime:
  store: KnowledgeStore
  traces: TraceStore
  chunks: list
  retriever: HybridRetriever
  service: RagService
  embedder: OpenAIEmbedder | None
  missingVectors: int


def buildRuntime(projectPath, settings, reranker=None):
  store = KnowledgeStore(settings.dataDir / "knowledge.db")
  seedChunks = loadChunks(projectPath / "data/seed/official_faq.jsonl")
  store.upsert([part for chunk in seedChunks for part in splitChunk(chunk)])
  chunks = publicChunks(store.listChunks())
  traces = TraceStore(settings.dataDir / "traces.db")
  client = OpenAIClient(settings.apiKey, settings.timeout) if settings.apiKey else None
  embedder = OpenAIEmbedder(client, settings.embeddingModel) if client else None
  vectors = store.loadVectors(embedder.modelId, chunks) if embedder else {}
  if settings.rerankerPath and reranker is None:
    reranker = LocalCrossEncoder(settings.rerankerPath)
  retriever = HybridRetriever(chunks, embedder if settings.retrievalMode == "hybrid" else None, vectors,
    reranker=reranker, minSimilarity=settings.minSimilarity)
  generator = OpenAIGenerator(client, settings.chatModel) if settings.provider == "openai" else ExtractiveGenerator()
  service = RagService(retriever, generator, traces, settings.maxContextChars)
  return Runtime(store, traces, chunks, retriever, service, embedder, len(chunks) - len(vectors))
