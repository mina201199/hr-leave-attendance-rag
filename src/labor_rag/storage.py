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

from .ingestion import validateChunk
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
      """)

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
    with self.connect() as connection:
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
          previousRecords, incomingRecords = json.loads(existing[0]), json.loads(payload)
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
        connection.execute("INSERT INTO documents VALUES (?, ?, ?) ON CONFLICT(documentId) DO UPDATE SET version=excluded.version, active=excluded.active", (documentId, version, group[0].active))
        if identity and group[0].active:
          storedCases[identity] = [(oldId, oldFields) for oldId, oldFields in storedCases[identity] if oldId != documentId]
          storedCases[identity].append((documentId, fields))

  def listChunks(self, includeInactive: bool = False, asOf: str | None = None) -> list[DocumentChunk]:
    asOf = asOf or datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    with self.connect() as connection:
      rows = connection.execute("SELECT r.documentId, r.version, r.payload, d.active FROM revisions r JOIN documents d ON r.documentId=d.documentId ORDER BY r.rowid").fetchall()
    currentVersions = {}
    decodedRows = [(documentId, version, json.loads(payload), enabled) for documentId, version, payload, enabled in rows]
    for documentId, version, records, enabled in decodedRows:
      if not records[0]["effectiveFrom"] or records[0]["effectiveFrom"] <= asOf:
        currentVersions[documentId] = version
    chunks = []
    for documentId, version, records, enabled in decodedRows:
      active = enabled and currentVersions.get(documentId) == version and (not records[0]["effectiveTo"] or asOf <= records[0]["effectiveTo"])
      if not active and not includeInactive:
        continue
      for record in records:
        record["allowedRoles"] = tuple(record["allowedRoles"])
        chunks.append(replace(DocumentChunk(**record), active=bool(active)))
    return chunks

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
