import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from dotenv import dotenv_values

from labor_rag.ingestion import ENTERPRISE_SOURCE_TYPES, loadChunks
from labor_rag.processing import splitChunk
from labor_rag.storage import KnowledgeStore


def prepareEnterprise(dbPath=None, importData=False, dataDir=None):
  values = {**dotenv_values(projectPath / ".env"), **os.environ}
  enterpriseDir = Path(dataDir or values.get("RAG_ENTERPRISE_DATA_DIR") or "data/index/enterprise")
  if not enterpriseDir.is_absolute():
    enterpriseDir = projectPath / enterpriseDir
  dbPath = Path(dbPath) if dbPath is not None else enterpriseDir / "knowledge.db"
  dbPath = dbPath.resolve()
  legacyDir = Path(values.get("RAG_DATA_DIR") or "data/index")
  if not legacyDir.is_absolute():
    legacyDir = projectPath / legacyDir
  if dbPath in {(projectPath / "data/index/knowledge.db").resolve(), (legacyDir / "knowledge.db").resolve()}:
    raise ValueError("企業資料庫必須與原版資料庫分開")
  # 僅匯入版本控制內明確標示虛構的示範檔，不接受任意上傳路徑。
  records = loadChunks(projectPath / "data/company_demo/knowledge.jsonl")
  records += loadChunks(projectPath / "data/company_demo/scenarios.jsonl")
  if any(not chunk.isDemo or "虛構" not in chunk.text or "虛構" not in chunk.title
      or chunk.sourceType not in ENTERPRISE_SOURCE_TYPES or chunk.externalProcessingAllowed for chunk in records):
    raise ValueError("示範匯入只允許禁止外送且明確標示虛構的企業制度")
  revisions = defaultdict(list)
  for chunk in records:
    revisions[(chunk.documentId, chunk.version)].extend(splitChunk(chunk))
  if importData:
    if dbPath.exists():
      # 在 KnowledgeStore 建表或遷移前唯讀檢查，避免把真實或原版資料庫當成示範庫。
      try:
        with closing(sqlite3.connect(dbPath.as_uri() + "?mode=ro", uri=True)) as connection:
          existingRecords = [record for (payload,) in connection.execute("SELECT payload FROM revisions")
            for record in json.loads(payload)]
          if any(record.get("isDemo") is not True or record.get("sourceType") not in ENTERPRISE_SOURCE_TYPES for record in existingRecords):
            raise ValueError("示範匯入不可操作含非虛構企業資料的資料庫")
      except (sqlite3.DatabaseError, json.JSONDecodeError) as error:
        raise ValueError("目標不是有效的獨立企業示範資料庫") from error
    store = KnowledgeStore(dbPath)
    # 每版完整匯入，保留不可變 payload 及獨立的發布／撤回狀態。
    for revision in revisions.values():
      store.upsert(revision)
  return {"documentCount": len({chunk.documentId for chunk in records}), "revisionCount": len(revisions),
    "chunkCount": sum(len(revision) for revision in revisions.values()), "databasePath": str(dbPath),
    "isDemo": True, "imported": bool(importData)}


def main():
  parser = argparse.ArgumentParser(description="檢查虛構企業示範語料；--import 僅匯入獨立企業 SQLite，不呼叫模型")
  parser.add_argument("--import", dest="importData", action="store_true")
  parser.add_argument("--data-dir", type=Path, help="企業資料目錄（預設 RAG_ENTERPRISE_DATA_DIR 或 data/index/enterprise）")
  args = parser.parse_args()
  print(json.dumps(prepareEnterprise(importData=args.importData, dataDir=args.data_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()
