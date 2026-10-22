import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ingestion import ENTERPRISE_SOURCE_TYPES, PUBLICATION_STATUSES, validateChunk, validateChunkGroups
from .models import DocumentChunk


def penaltyIdentity(records):
  if not records or records[0]["sourceType"] != "裁罰紀錄":
    return None, None
  fields = set()
  allowedFields = {"公告日期", "處分日期", "處分字號", "違反勞動基準法條款", "違反法規內容", "罰鍰金額", "備註"}
  for text in {item.get("parentText") or item["text"] for item in records}:
    for line in text.splitlines():
      name, separator, value = line.partition("：")
      if separator and name.strip() in allowedFields:
        fields.add((name.strip(), value.strip()))
  caseNumbers = {value for name, value in fields if name == "處分字號"}
  if len(caseNumbers) != 1 or not records[0].get("companyName"):
    return None, None
  record = records[0]
  identity = (record["publisher"], record["companyName"], next(iter(caseNumbers)), tuple(record["allowedRoles"]))
  return identity, fields


def embeddingText(chunk: DocumentChunk) -> str:
  return chunk.title + "\n" + chunk.text


def contentHash(chunk: DocumentChunk) -> str:
  return hashlib.sha256(embeddingText(chunk).encode("utf-8")).hexdigest()


def validateVector(vector: list[float]) -> None:
  if not vector or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
    raise ValueError("向量必須包含有限數值")
  norm = math.hypot(*vector)
  if not math.isfinite(norm) or norm == 0:
    raise ValueError("向量長度不可為零或無限")


class KnowledgeStore:
  def __init__(self, path: str | Path):
    self.path = Path(path)
    self.path.parent.mkdir(parents=True, exist_ok=True)
    with self.connect() as connection:
      connection.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
          documentId TEXT PRIMARY KEY, version TEXT NOT NULL, active INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS revisions (
          documentId TEXT NOT NULL, version TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (documentId, version)
        );
        CREATE TABLE IF NOT EXISTS vectors (
          modelId TEXT NOT NULL, chunkId TEXT NOT NULL, version TEXT NOT NULL,
          contentHash TEXT NOT NULL, vector TEXT NOT NULL,
          PRIMARY KEY (modelId, chunkId, version, contentHash)
        );
        CREATE TABLE IF NOT EXISTS revisionStates (
          documentId TEXT NOT NULL, version TEXT NOT NULL,
          publicationStatus TEXT NOT NULL CHECK(publicationStatus IN ('draft', 'published', 'withdrawn')),
          PRIMARY KEY (documentId, version),
          FOREIGN KEY (documentId, version) REFERENCES revisions(documentId, version)
        );
      """)
      # 狀態回填使用同一交易，保留原始 payload、歷史及向量，不改寫既有文件。
      historicalChunks = []
      for documentId, version, payload in connection.execute("SELECT documentId, version, payload FROM revisions"):
        records = [DocumentChunk(**record) for record in json.loads(payload)]
        if not records:
          raise ValueError("文件版本不可沒有片段")
        for chunk in records:
          validateChunk(chunk)
        historicalChunks.extend(records)
        validateChunkGroups(records)
        connection.execute("INSERT OR IGNORE INTO revisionStates VALUES (?, ?, ?)",
          (documentId, version, records[0].publicationStatus))
      validateChunkGroups(historicalChunks)

  @contextmanager
  def connect(self):
    connection = sqlite3.connect(self.path, timeout=10)
    try:
      with connection:
        yield connection
    finally:
      connection.close()

  def upsert(self, chunks: list[DocumentChunk]) -> None:
    groups = defaultdict(list)
    if len({chunk.chunkId for chunk in chunks}) != len(chunks):
      raise ValueError("匯入批次含重複片段 ID")
    for chunk in chunks:
      validateChunk(chunk)
      groups[chunk.documentId].append(chunk)
    validateChunkGroups(chunks)
    with self.connect() as connection:
      # 已存父群組也須檢查，避免分批匯入藉共用父 ID 混入其他權限的全文。
      previousChunks = [DocumentChunk(**record) for (payload,) in connection.execute("SELECT payload FROM revisions")
        for record in json.loads(payload)]
      incomingKeys = {(chunk.documentId, chunk.version) for chunk in chunks}
      validateChunkGroups([chunk for chunk in previousChunks if (chunk.documentId, chunk.version) not in incomingKeys] + chunks)
      storedCases = defaultdict(list)
      for oldId, oldPayload in connection.execute("SELECT d.documentId, r.payload FROM documents d JOIN revisions r ON r.documentId=d.documentId AND r.version=d.version WHERE d.active=1"):
        identity, fields = penaltyIdentity(json.loads(oldPayload))
        if identity:
          storedCases[identity].append((oldId, fields))
      for documentId, group in groups.items():
        if len({(item.version, item.active, tuple(item.allowedRoles), item.effectiveFrom, item.effectiveTo) for item in group}) != 1:
          raise ValueError("同一文件的版本、權限與有效期間必須一致")
        version = group[0].version
        payload = json.dumps([asdict(item) for item in sorted(group, key=lambda item: item.chunkId)], ensure_ascii=False, sort_keys=True)
        existing = connection.execute("SELECT payload FROM revisions WHERE documentId=? AND version=?", (documentId, version)).fetchone()
        if existing:
          previousRecords = [asdict(DocumentChunk(**record)) for record in json.loads(existing[0])]
          incomingRecords = [asdict(DocumentChunk(**record)) for record in json.loads(payload)]
          for record in previousRecords + incomingRecords:
            record.pop("collectedAt", None)
          if previousRecords != incomingRecords:
            raise ValueError("文件內容已變更，請使用新的版本，並匯入該文件的完整片段")
          continue
        identity, fields = penaltyIdentity(json.loads(payload))
        aliases = [(oldId, oldFields) for oldId, oldFields in storedCases.get(identity, []) if oldId != documentId]
        if aliases:
          if any(oldFields != fields for oldId, oldFields in aliases):
            raise ValueError("同一裁罰案件的公告內容不同，請核對後以原文件 ID 的新版本匯入")
          # 將舊全文雜湊 ID 收斂到處分字號 ID，歷史資料仍保留。
          if documentId.startswith("taipei-case-") and all(oldId.startswith("penalty-") for oldId, oldFields in aliases):
            for oldId, oldFields in aliases:
              connection.execute("UPDATE documents SET active=0 WHERE documentId=?", (oldId,))
            storedCases[identity] = []
          else:
            continue
        for (previousPayload,) in connection.execute("SELECT payload FROM revisions WHERE documentId<>?", (documentId,)):
          if {item['chunkId'] for item in json.loads(previousPayload)} & {item.chunkId for item in group}:
            raise ValueError("片段 ID 已由其他文件使用")
        connection.execute("INSERT INTO revisions VALUES (?, ?, ?)", (documentId, version, payload))
        connection.execute("INSERT INTO revisionStates VALUES (?, ?, ?)", (documentId, version, group[0].publicationStatus))
        activeUpdate = "documents.active" if group[0].sourceType in ENTERPRISE_SOURCE_TYPES else "excluded.active"
        connection.execute("INSERT INTO documents VALUES (?, ?, ?) ON CONFLICT(documentId) DO UPDATE SET version=excluded.version, active=" + activeUpdate,
          (documentId, version, group[0].active))
        if identity and group[0].active:
          storedCases[identity] = [(oldId, oldFields) for oldId, oldFields in storedCases[identity] if oldId != documentId]
          storedCases[identity].append((documentId, fields))

  def listChunks(self, includeInactive: bool = False, asOf: str | None = None) -> list[DocumentChunk]:
    asOf = asOf or datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    with self.connect() as connection:
      rows = connection.execute("SELECT r.documentId, r.version, r.payload, d.active, s.publicationStatus FROM revisions r JOIN documents d ON r.documentId=d.documentId JOIN revisionStates s ON s.documentId=r.documentId AND s.version=r.version ORDER BY r.rowid").fetchall()
    currentVersions = {}
    decodedRows = [(documentId, version, json.loads(payload), enabled, status) for documentId, version, payload, enabled, status in rows]
    for documentId, version, records, enabled, status in decodedRows:
      effectiveFrom = records[0].get("effectiveFrom") or ""
      if status != "draft" and effectiveFrom <= asOf:
        previous = currentVersions.get(documentId)
        if records[0]["sourceType"] not in ENTERPRISE_SOURCE_TYPES or previous is None or effectiveFrom >= previous[0]:
          currentVersions[documentId] = (effectiveFrom, version)
    chunks = []
    for documentId, version, records, enabled, status in decodedRows:
      active = enabled and status == "published" and currentVersions.get(documentId, (None, None))[1] == version and (not records[0].get("effectiveTo") or asOf <= records[0]["effectiveTo"])
      if not active and not includeInactive:
        continue
      for record in records:
        record["allowedRoles"] = tuple(record["allowedRoles"])
        chunks.append(replace(DocumentChunk(**record), active=bool(active), publicationStatus=status))
    return chunks

  def setPublicationStatus(self, documentId: str, version: str, status: str) -> None:
    if status not in PUBLICATION_STATUSES:
      raise ValueError("publicationStatus 必須為 draft、published 或 withdrawn")
    with self.connect() as connection:
      # 在更新時限制狀態轉換，避免未發布草稿遮蔽現行版，或已淘汰舊版重新出現。
      cursor = connection.execute("UPDATE revisionStates SET publicationStatus=? WHERE documentId=? AND version=? AND (?<>'draft' OR publicationStatus='draft') AND (?<>'withdrawn' OR publicationStatus<>'draft')",
        (status, documentId, version, status, status))
      if cursor.rowcount != 1:
        if connection.execute("SELECT 1 FROM revisionStates WHERE documentId=? AND version=?", (documentId, version)).fetchone():
          raise ValueError("草稿尚未發布，不可撤回" if status == "withdrawn" else "已發布或撤回的版本不可退回草稿")
        raise ValueError("找不到文件版本")

  def setActive(self, documentId: str, active: bool) -> None:
    with self.connect() as connection:
      cursor = connection.execute("UPDATE documents SET active=? WHERE documentId=?", (active, documentId))
      if cursor.rowcount != 1:
        raise ValueError("找不到文件")

  def saveVectors(self, modelId: str, chunks: list[DocumentChunk], vectors: list[list[float]]) -> None:
    if len(chunks) != len(vectors):
      raise ValueError("片段與向量數量不一致")
    for vector in vectors:
      validateVector(vector)
    if len({len(vector) for vector in vectors}) > 1:
      raise ValueError("向量維度不一致")
    with self.connect() as connection:
      connection.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?)", [
        (modelId, chunk.chunkId, chunk.version, contentHash(chunk), json.dumps(vector))
        for chunk, vector in zip(chunks, vectors)
      ])

  def loadVectors(self, modelId: str, chunks: list[DocumentChunk]) -> dict[str, list[float]]:
    vectors = {}
    with self.connect() as connection:
      for chunk in chunks:
        row = connection.execute("SELECT vector FROM vectors WHERE modelId=? AND chunkId=? AND version=? AND contentHash=?", (modelId, chunk.chunkId, chunk.version, contentHash(chunk))).fetchone()
        if row:
          vector = json.loads(row[0])
          validateVector(vector)
          vectors[chunk.chunkId] = vector
    return vectors
