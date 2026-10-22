import hashlib
from dataclasses import replace
from time import perf_counter
from uuid import uuid4

from .context import buildContext
from .generation import AnswerGenerator, ExtractiveGenerator
from .models import AnswerResponse
from .providers import PROMPT_VERSION
from .retrieval import Retriever


class RagService:
  def __init__(self, retriever: Retriever, generator: AnswerGenerator | None = None, traceStore=None, maxContextChars=8000):
    self.retriever = retriever
    self.generator = generator if generator is not None else ExtractiveGenerator()
    self.traceStore = traceStore
    self.maxContextChars = maxContextChars

  def ask(self, question: str, topK: int = 3, category: str | None = None, companyName: str | None = None) -> AnswerResponse:
    question = question.strip()
    companyName = companyName.strip() if companyName else None
    if not question and companyName:
      question = "裁罰紀錄"
    if not question or len(question) > 1000:
      raise ValueError("請輸入 1 至 1000 字的問題")
    started = perf_counter()
    traceId = uuid4().hex
    payload = {"questionHash": hashlib.sha256(question.encode("utf-8")).hexdigest(), "questionLength": len(question),
      "mode": self.generator.mode, "model": getattr(self.generator, "model", None), "promptVersion": PROMPT_VERSION,
      "retrievalMode": getattr(self.retriever, "mode", "BM25 關鍵字"), "topK": topK}
    try:
      retrievalStarted = perf_counter()
      hits = self.retriever.search(question, topK, category, companyName)
      payload["retrievalMs"] = round((perf_counter() - retrievalStarted) * 1000, 2)
      payload["retrievedIds"] = [hit.chunk.chunkId for hit in hits]
      contextHits = buildContext(hits, self.maxContextChars)
      payload["contextIds"] = [hit.chunk.chunkId for hit in contextHits]
      payload["sourceVersions"] = {hit.chunk.chunkId: hit.chunk.version for hit in contextHits}
      payload["embeddingUsage"] = getattr(self.retriever, "lastUsage", {})
      if hits and not contextHits:
        response = AnswerResponse("context_limit", "找到相關資料，但完整來源段落超過內容預算，尚未產生回答。請查看下方來源，或由資料維護者依完整條款重新分段。", [], self.generator.mode)
        usage = {}
      else:
        response, usage = self.answer(question, contextHits)
      payload.update(status=response.status, citationIds=[chunk.chunkId for chunk in response.citations], generationUsage=usage)
      response = replace(response, hits=hits, traceId=traceId, elapsedMs=round((perf_counter() - started) * 1000, 2))
      return response
    except Exception as error:
      payload.update(status="error", errorType=type(error).__name__)
      raise
    finally:
      payload["elapsedMs"] = round((perf_counter() - started) * 1000, 2)
      if self.traceStore is not None:
        self.traceStore.record(traceId, payload)

  def answer(self, question, hits):
    if not hits:
      return AnswerResponse(
        status="no_evidence", text="目前資料中找不到相關內容，無法提供有來源支持的回答。請調整問題或補充資料。",
        citations=[], mode=self.generator.mode,
      ), {}
    generated = self.generator.generate(question, hits)
    if not generated.answerable:
      return AnswerResponse("no_evidence", "檢索到的資料不足以支持這個問題，無法提供有來源支持的回答。", [], self.generator.mode), generated.usage
    availableChunks = {hit.chunk.chunkId: hit.chunk for hit in hits}
    if not generated.text.strip() or not generated.citationIds or any(chunkId not in availableChunks for chunkId in generated.citationIds):
      raise ValueError("回答的引用無效，請檢查生成器")
    citations = [availableChunks[chunkId] for chunkId in dict.fromkeys(generated.citationIds)]
    return AnswerResponse(status="answered", text=generated.text, citations=citations, mode=self.generator.mode), generated.usage
