import math
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import SearchHit
from .retrieval import Retriever
from .storage import validateVector


def fuseRanks(rankings: list[list[SearchHit]], constant: int = 60) -> list[SearchHit]:
  scores, chunks = {}, {}
  for ranking in rankings:
    seen = set()
    for rank, hit in enumerate(ranking, 1):
      chunkId = hit.chunk.chunkId
      if chunkId in seen:
        continue
      seen.add(chunkId)
      chunks[chunkId] = hit.chunk
      scores[chunkId] = scores.get(chunkId, 0) + 1 / (constant + rank)
  return sorted([SearchHit(chunks[key], score) for key, score in scores.items()], key=lambda hit: (-hit.score, hit.chunk.chunkId))


class HybridRetriever:
  def __init__(self, chunks, embedder=None, vectors=None, reranker=None, accessRole="public", asOf=None, minSimilarity=0.35):
    self.chunks = chunks
    self.embedder = embedder
    self.vectors = vectors or {}
    self.reranker = reranker
    self.accessRole = accessRole
    self.asOf = asOf
    self.minSimilarity = minSimilarity
    self.lastUsage = {}
    self.mode = "BM25 + 向量 + RRF" if embedder is not None else "BM25 關鍵字"

  def search(self, question, topK=3, category=None, companyName=None):
    if not 1 <= topK <= 10:
      raise ValueError("topK 必須介於 1 與 10")
    self.lastUsage = {}
    asOf = self.asOf or datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    eligible = [chunk for chunk in self.chunks if chunk.active and self.accessRole in chunk.allowedRoles
      and (not chunk.effectiveFrom or chunk.effectiveFrom <= asOf)
      and (not chunk.effectiveTo or asOf <= chunk.effectiveTo)
      and (not category or chunk.category == category)
      and (not companyName or chunk.companyName == companyName.strip())]
    if not eligible:
      return []
    lexical = Retriever(eligible).search(question, topK=10, companyName=companyName)
    rankings = [lexical]
    if self.embedder is not None:
      if any(chunk.chunkId not in self.vectors for chunk in eligible):
        raise ValueError("向量索引尚未完整，請先建立／更新索引")
      queryVector = self.embedder.embed([question])[0]
      self.lastUsage = getattr(self.embedder, "lastUsage", {})
      validateVector(queryVector)
      queryNorm = math.hypot(*queryVector)
      semantic = []
      for chunk in eligible:
        vector = self.vectors[chunk.chunkId]
        validateVector(vector)
        if len(vector) != len(queryVector):
          raise ValueError("查詢向量與文件向量維度不一致，請重建索引")
        vectorNorm = math.hypot(*vector)
        score = sum(a / vectorNorm * b / queryNorm for a, b in zip(vector, queryVector))
        if score >= self.minSimilarity:
          semantic.append(SearchHit(chunk, score))
      rankings.append(sorted(semantic, key=lambda hit: (-hit.score, hit.chunk.chunkId))[:10])
    hits = fuseRanks(rankings)
    if self.reranker is not None and hits:
      hits = self.reranker.rerank(question, hits)
    return hits[:topK]


class LocalCrossEncoder:
  def __init__(self, modelPath):
    from pathlib import Path
    if not Path(modelPath).is_dir():
      raise ValueError("Reranker 必須指定已下載的本機模型資料夾")
    from sentence_transformers import CrossEncoder
    self.model = CrossEncoder(str(modelPath), local_files_only=True, trust_remote_code=False)

  def rerank(self, question, hits):
    scores = self.model.predict([(question, hit.chunk.text) for hit in hits])
    if len(scores) != len(hits) or any(not math.isfinite(float(score)) for score in scores):
      raise ValueError("Reranker 回傳無效分數")
    return sorted([SearchHit(hit.chunk, float(score)) for hit, score in zip(hits, scores)], key=lambda hit: (-hit.score, hit.chunk.chunkId))
