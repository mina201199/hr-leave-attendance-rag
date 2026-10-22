import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .auth import AuthStore, AuthorizationError
from .enterprise_generation import EnterpriseExtractiveGenerator, EnterpriseOpenAIGenerator, validateGenerated
from .hybrid import HybridRetriever
from .ingestion import ENTERPRISE_SOURCE_TYPES, validateChunk, validateChunkGroups
from .models import AnswerResponse, DocumentChunk, SearchHit
from .policy import canRead, requireManage
from .processing import splitChunk
from .providers import OpenAIClient, OpenAIEmbedder
from .storage import KnowledgeStore, embeddingText


def digest(value):
  return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def contextChunk(chunk):
  return replace(chunk, text=chunk.parentText or chunk.text)


def scopedContext(hits, maxChars):
  result, seen, used = [], set(), 0
  for hit in hits:
    chunk = hit.chunk
    identity = (chunk.documentId, chunk.version, chunk.parentId or chunk.chunkId, tuple(chunk.allowedRoles))
    expanded = contextChunk(chunk)
    if identity in seen or used + len(expanded.text) > maxChars:
      continue
    seen.add(identity)
    used += len(expanded.text)
    result.append(SearchHit(expanded, hit.score))
  return result


class EnterpriseService:
  def __init__(self, authStore, store, generator=None, embedder=None, retrievalMode="bm25", maxContextChars=8000, tracePath=None):
    self.authStore = authStore
    self.store = store
    self.generator = generator if generator is not None else EnterpriseExtractiveGenerator()
    self.embedder = embedder
    if retrievalMode not in ("bm25", "hybrid") or not 100 <= maxContextChars <= 100000:
      raise ValueError("企業檢索或內容預算設定無效")
    self.retrievalMode = retrievalMode
    self.maxContextChars = maxContextChars
    self.tracePath = Path(tracePath) if tracePath is not None else store.path.parent / "enterprise-traces.db"
    if self.tracePath.resolve() in (store.path.resolve(), authStore.path.resolve()):
      raise ValueError("查詢紀錄必須使用獨立資料庫")
    self.tracePath.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(self.tracePath)) as connection, connection:
      connection.execute("CREATE TABLE IF NOT EXISTS enterpriseTraces (traceId TEXT PRIMARY KEY, ownerId TEXT NOT NULL, createdAt TEXT NOT NULL, payload TEXT NOT NULL, feedback TEXT)")

  def _date(self, asOf):
    asOf = asOf or datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    if not isinstance(asOf, str) or date.fromisoformat(asOf).isoformat() != asOf:
      raise ValueError("查詢日期必須為 YYYY-MM-DD")
    return asOf

  def _eligible(self, principal, asOf):
    chunks = self.store.listChunks(asOf=asOf)
    validateChunkGroups(chunks)
    return [chunk for chunk in chunks if canRead(principal, chunk)]

  def _revalidate(self, token, chunks, asOf):
    principal = self.authStore.authenticate(token)
    available = {(chunk.chunkId, chunk.version): chunk for chunk in self._eligible(principal, asOf)}
    for chunk in chunks:
      if available.get((chunk.chunkId, chunk.version)) != chunk:
        raise AuthorizationError()
    return principal

  def _externalPermission(self, provider, chunks):
    if getattr(provider, "externalProcessing", True) is not False and any(not chunk.externalProcessingAllowed for chunk in chunks):
      raise ValueError("來源制度未允許外送，無法使用雲端 embedding 或模型回答；請使用離線來源摘錄")

  def _references(self, response):
    refs = []
    for chunk, expanded in ([(hit.chunk, False) for hit in response.hits] + [(chunk, True) for chunk in response.citations]):
      reference = {"chunkId": chunk.chunkId, "version": chunk.version, "contentHash": digest(asdict(chunk)), "expanded": expanded}
      if reference not in refs:
        refs.append(reference)
    return refs

  def _checkReferences(self, token, refs, asOf):
    principal = self.authStore.authenticate(token)
    chunks = {(chunk.chunkId, chunk.version): chunk for chunk in self._eligible(principal, asOf)}
    for reference in refs:
      chunk = chunks.get((reference["chunkId"], reference["version"]))
      if chunk is None:
        raise AuthorizationError()
      chunk = contextChunk(chunk) if reference["expanded"] else chunk
      if digest(asdict(chunk)) != reference["contentHash"]:
        raise AuthorizationError()

  def _trace(self, principal, traceId):
    with closing(sqlite3.connect(self.tracePath)) as connection, connection:
      row = connection.execute("SELECT payload FROM enterpriseTraces WHERE traceId=? AND ownerId=?", (traceId, principal.userId)).fetchone()
    if row is None:
      raise AuthorizationError()
    return json.loads(row[0])

  def ask(self, token, question, asOf=None, category=None, topK=3):
    principal = self.authStore.authenticate(token)
    asOf = self._date(asOf)
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
      raise ValueError("請輸入 1 至 1000 字的問題")
    if type(topK) is not int or not 1 <= topK <= 10:
      raise ValueError("topK 必須介於 1 與 10")
    question = question.strip()
    started = perf_counter()
    eligible = [chunk for chunk in self._eligible(principal, asOf) if category is None or chunk.category == category]
    # 個人餘額並未接上任何資料，不能把制度摘錄誤呈現為個人查詢結果。
    personalBalance = re.search(r"(我|本人|個人).*(剩|餘額|餘假|假額)|剩餘.*(特休|假)|還有多少.*(假|特休)", question)
    personalBalance = personalBalance or (re.search(r"我|本人|個人", question) and re.search(r"特休|年假|假額|休假", question)
      and re.search(r"多少|幾天|幾小時|有幾|能休|可休", question))
    if personalBalance:
      hits = []
    else:
      queryEmbedder = None
      vectors = {}
      if self.retrievalMode == "hybrid":
        if self.embedder is None:
          raise ValueError("請先設定企業 embedding provider 並建立索引")
        service = self

        class GuardedEmbedder:
          def embed(self, texts):
            service._revalidate(token, eligible, asOf)
            service._externalPermission(service.embedder, eligible)
            result = service.embedder.embed(texts)
            service._revalidate(token, eligible, asOf)
            return result

        queryEmbedder = GuardedEmbedder()
        vectors = self.store.loadVectors(self.embedder.modelId, eligible)
      # 公開來源通過 canRead 後才正規化副本；原始 ACL 仍保留於回應與重驗。
      copies = [replace(chunk, allowedRoles=(principal.role,)) for chunk in eligible]
      retriever = HybridRetriever(copies, queryEmbedder, vectors, accessRole=principal.role, asOf=asOf)
      byId = {chunk.chunkId: chunk for chunk in eligible}
      hits = [SearchHit(byId[hit.chunk.chunkId], hit.score) for hit in retriever.search(question, topK, category)]
    self._revalidate(token, [hit.chunk for hit in hits], asOf)
    contexts = scopedContext(hits, self.maxContextChars)
    status, text, citations = "no_evidence", "目前沒有可支持此問題的來源；本系統不查詢個人餘額或出勤紀錄。", []
    if hits and not contexts:
      status, text = "context_limit", "完整來源超過內容預算，請調整問題或由維護者依完整條款重新分段。"
    elif contexts:
      originals = [hit.chunk for hit in hits]
      self._revalidate(token, originals, asOf)
      self._externalPermission(self.generator, originals)
      generated = self.generator.generate(question, contexts)
      self._revalidate(token, originals, asOf)
      generated = validateGenerated(generated, contexts)
      status = generated.status
      defaults = {"no_evidence": "來源不足，無法提供有依據的回答。", "needs_clarification": "適用條件不足，請補充問題條件。", "conflict": "來源條件有衝突，請由制度維護者確認。"}
      lines, ids = [], []
      for claim in generated.claims:
        lines.append(claim.text.strip() + " " + " ".join(f"[{chunkId}]" for chunkId in dict.fromkeys(claim.citationIds)))
        ids.extend(claim.citationIds)
      text = "\n\n".join(lines) or defaults.get(status, "")
      if getattr(self.generator, "externalProcessing", True) is False:
        text = "【來源摘錄，非模型回答；虛構示範依各來源標示】\n\n" + text
      available = {hit.chunk.chunkId: hit.chunk for hit in contexts}
      citations = [available[chunkId] for chunkId in dict.fromkeys(ids)]
    response = AnswerResponse(status, text, citations, self.generator.mode, hits, uuid4().hex, round((perf_counter() - started) * 1000, 2))
    principal = self._revalidate(token, [hit.chunk for hit in hits], asOf)
    payload = {"questionHash": hashlib.sha256(question.encode("utf-8")).hexdigest(), "asOf": asOf,
      "mode": response.mode, "status": response.status, "references": self._references(response), "responseHash": digest(asdict(response))}
    with closing(sqlite3.connect(self.tracePath)) as connection, connection:
      connection.execute("INSERT INTO enterpriseTraces VALUES (?, ?, ?, ?, NULL)",
        (response.traceId, principal.userId, datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)))
    return response

  def listSources(self, token, asOf=None):
    principal = self.authStore.authenticate(token)
    return self._eligible(principal, self._date(asOf))

  def readCitation(self, token, chunkId, asOf=None, version=None):
    principal = self.authStore.authenticate(token)
    for chunk in self._eligible(principal, self._date(asOf)):
      if chunk.chunkId == chunkId and (version is None or chunk.version == version):
        return contextChunk(chunk)
    raise AuthorizationError()

  def validateResponse(self, token, response, asOf=None):
    principal = self.authStore.authenticate(token)
    if not isinstance(response, AnswerResponse) or not response.traceId:
      raise AuthorizationError()
    payload = self._trace(principal, response.traceId)
    if digest(asdict(response)) != payload["responseHash"]:
      raise AuthorizationError()
    self._checkReferences(token, payload["references"], self._date(asOf or payload["asOf"]))
    return True

  def recentTraces(self, token):
    principal = self.authStore.authenticate(token)
    with closing(sqlite3.connect(self.tracePath)) as connection, connection:
      rows = connection.execute("SELECT traceId, createdAt, payload, feedback FROM enterpriseTraces WHERE ownerId=? ORDER BY createdAt DESC LIMIT 50", (principal.userId,)).fetchall()
    result = []
    for traceId, createdAt, raw, feedback in rows:
      payload = json.loads(raw)
      try:
        self._checkReferences(token, payload["references"], payload["asOf"])
      except AuthorizationError:
        continue
      result.append({"traceId": traceId, "createdAt": createdAt, "questionHash": payload["questionHash"],
        "asOf": payload["asOf"], "mode": payload["mode"], "status": payload["status"],
        "sourceVersions": [{"chunkId": ref["chunkId"], "version": ref["version"]} for ref in payload["references"] if not ref["expanded"]], "feedback": feedback})
    return result

  def feedback(self, token, traceId, rating):
    principal = self.authStore.authenticate(token)
    payload = self._trace(principal, traceId)
    self._checkReferences(token, payload["references"], payload["asOf"])
    if rating not in ("helpful", "unhelpful"):
      raise ValueError("不支援的回饋")
    with closing(sqlite3.connect(self.tracePath)) as connection, connection:
      connection.execute("UPDATE enterpriseTraces SET feedback=? WHERE traceId=? AND ownerId=?", (rating, traceId, principal.userId))

  def _manager(self, token):
    principal = self.authStore.authenticate(token)
    requireManage(principal)
    return principal

  def _manageScope(self, principal, chunk):
    return chunk.sourceType in ENTERPRISE_SOURCE_TYPES and canRead(principal, replace(chunk, active=True, publicationStatus="published"))

  def previewImport(self, token, jsonlText):
    principal = self._manager(token)
    if not isinstance(jsonlText, str) or not jsonlText.strip() or len(jsonlText) > 2_000_000:
      raise ValueError("請提供不超過 2 MB 的 JSONL")
    chunks = []
    try:
      for line in jsonlText.lstrip("\ufeff").splitlines():
        if not line.strip():
          continue
        chunk = replace(DocumentChunk(**json.loads(line)), publicationStatus="draft")
        validateChunk(chunk)
        if not self._manageScope(principal, chunk):
          raise AuthorizationError()
        chunks.extend(splitChunk(replace(chunk, allowedRoles=tuple(chunk.allowedRoles))))
      validateChunkGroups(chunks)
      if len({chunk.chunkId for chunk in chunks}) != len(chunks):
        raise ValueError()
    except (TypeError, ValueError):
      raise ValueError("匯入格式或中繼資料無效；內部文件必須有明確 ACL、維護者與生效日") from None
    return chunks

  def importDocuments(self, token, jsonlText):
    self._manager(token)
    chunks = self.previewImport(token, jsonlText)
    principal = self._manager(token)
    existing = self.store.listChunks(includeInactive=True)
    incomingDocuments = {chunk.documentId for chunk in chunks}
    if any(not self._manageScope(principal, chunk) for chunk in existing if chunk.documentId in incomingDocuments):
      raise AuthorizationError()
    revisions = {(chunk.documentId, chunk.version) for chunk in chunks}
    if len(incomingDocuments) != len(revisions):
      raise ValueError("每次匯入同一文件的一個完整版本")
    self.store.upsert(chunks)
    return {"documentCount": len(incomingDocuments), "revisionCount": len(revisions), "chunkCount": len(chunks)}

  def listManagedDocuments(self, token):
    principal = self._manager(token)
    return [chunk for chunk in self.store.listChunks(includeInactive=True) if self._manageScope(principal, chunk)]

  def setPublicationStatus(self, token, documentId, version, status):
    principal = self._manager(token)
    chunks = [chunk for chunk in self.store.listChunks(includeInactive=True) if chunk.documentId == documentId and chunk.version == version]
    if not chunks or any(not self._manageScope(principal, chunk) for chunk in chunks):
      raise AuthorizationError()
    self.store.setPublicationStatus(documentId, version, status)

  def indexMissing(self, token, asOf=None):
    principal = self._manager(token)
    if self.embedder is None:
      raise ValueError("尚未設定企業 embedding provider")
    asOf = self._date(asOf)
    chunks = self._eligible(principal, asOf)
    cached = self.store.loadVectors(self.embedder.modelId, chunks)
    pending = [chunk for chunk in chunks if chunk.chunkId not in cached]
    self._externalPermission(self.embedder, pending)
    indexed, totalTokens = 0, 0
    for start in range(0, len(pending), 32):
      batch = pending[start:start + 32]
      self._manager(token)
      self._revalidate(token, batch, asOf)
      self._externalPermission(self.embedder, batch)
      vectors = self.embedder.embed([embeddingText(chunk) for chunk in batch])
      self._manager(token)
      self._revalidate(token, batch, asOf)
      self.store.saveVectors(self.embedder.modelId, batch, vectors)
      indexed += len(batch)
      totalTokens += getattr(self.embedder, "lastUsage", {}).get("total_tokens", 0)
    return {"indexed": indexed, "cached": len(cached), "totalTokens": totalTokens}


def buildEnterpriseRuntime(projectPath, settings):
  projectPath = Path(projectPath).resolve()
  values = {**dotenv_values(projectPath / ".env"), **os.environ}
  enterpriseDir = Path(values.get("RAG_ENTERPRISE_DATA_DIR") or "data/index/enterprise")
  if not enterpriseDir.is_absolute():
    enterpriseDir = projectPath / enterpriseDir
  enterpriseDir = enterpriseDir.resolve()
  legacyDir = Path(values.get("RAG_DATA_DIR") or "data/index")
  if not legacyDir.is_absolute():
    legacyDir = projectPath / legacyDir
  dbPath = enterpriseDir / "knowledge.db"
  legacyPaths = [directory / filename for directory in (projectPath / "data/index", legacyDir, settings.dataDir)
    for filename in ("knowledge.db", "traces.db")]
  enterprisePaths = [enterpriseDir / filename for filename in ("knowledge.db", "auth.db", "enterprise-traces.db")]
  for enterprisePath in enterprisePaths:
    for legacyPath in legacyPaths:
      if enterprisePath.resolve() == legacyPath.resolve() or (enterprisePath.exists() and legacyPath.exists() and enterprisePath.samefile(legacyPath)):
        raise ValueError("企業資料庫必須與原版資料庫分開")
  authStore = AuthStore(enterpriseDir / "auth.db")
  store = KnowledgeStore(dbPath)
  client = OpenAIClient(settings.apiKey, settings.timeout) if settings.apiKey else None
  embedder = OpenAIEmbedder(client, settings.embeddingModel) if client else None
  generator = EnterpriseOpenAIGenerator(client, settings.chatModel) if settings.provider == "openai" else EnterpriseExtractiveGenerator()
  return EnterpriseService(authStore, store, generator, embedder, settings.retrievalMode, settings.maxContextChars)
