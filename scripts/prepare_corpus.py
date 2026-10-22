import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.corpus import buildPenaltyCases, parseOfficialFaq
from labor_rag.ingestion import loadChunks
from labor_rag.processing import splitChunk
from labor_rag.runtime import buildRuntime
from labor_rag.settings import loadSettings


def saveSnapshot(path, text):
  path.parent.mkdir(parents=True, exist_ok=True)
  if path.exists() and path.read_text(encoding="utf-8") != text:
    raise ValueError(f"快照內容不同，請使用新的版本目錄：{path}")
  if not path.exists():
    path.write_text(text, encoding="utf-8")


def main():
  parser = argparse.ArgumentParser(description="由已下載的官方資料重建 120 筆專題語料；不呼叫模型")
  parser.add_argument("--import", dest="importData", action="store_true")
  args = parser.parse_args()
  collectedAt = "2026-10-21"
  rawPath = projectPath / "data/raw/expansion-20261021"
  outputPath = projectPath / "data/corpus/2026-10-21"
  selected = json.loads((rawPath / "faq-selection.json").read_text(encoding="utf-8"))
  faq = [parseOfficialFaq((rawPath / item["rawFile"]).read_text(encoding="utf-8-sig"), item, collectedAt) for item in selected]
  penalties = buildPenaltyCases((rawPath / "taipei-penalties.csv").read_text(encoding="utf-8-sig"), 80, collectedAt)
  if len(faq) != 40 or len({item.sourceUrl for item in faq}) != 40 or len({item.text for item in faq}) != 40:
    raise ValueError("官方問答必須為 40 筆不同來源、不同內容")
  records = faq + penalties
  if len({item.documentId for item in records}) != 120:
    raise ValueError("語料必須為 120 筆不同文件／案件")
  chunks = [part for item in records for part in splitChunk(item)]
  manifest = {"collectedAt": collectedAt, "recordCount": 120, "faqCount": 40, "penaltyCaseCount": 80,
    "chunkCount": len(chunks), "categories": dict(Counter(item.category for item in records)),
    "faqPublishers": dict(Counter(item.publisher for item in faq)),
    "selection": "人工選定 40 題薪資、工時、休假官方問答；裁罰限備註含工讀／部分工時專案，至少一項第21–28、30–43條違規，排除撤銷，依處分日期取最近80件，依處分字號合併。",
    "limitations": "官方問答為來源日期版本；非完整法規資料庫。裁罰是歷史公告，專案名稱不代表每案受罰事項均涉及工讀生。不推論目前違規；80件並非所有公司完整紀錄。",
    "sources": [{"documentId": item.documentId, "title": item.title, "sourceUrl": item.sourceUrl,
      "sourceUpdatedAt": item.sourceUpdatedAt, "sourceType": item.sourceType} for item in records],
    "rawFiles": [{"path": str(path.relative_to(projectPath)).replace("\\", "/"),
      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(rawPath.iterdir()) if path.is_file()]}
  saveSnapshot(outputPath / "knowledge.jsonl", "".join(json.dumps(asdict(item), ensure_ascii=False) + "\n" for item in records))
  saveSnapshot(outputPath / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
  result = {key: manifest[key] for key in ("recordCount", "faqCount", "penaltyCaseCount", "chunkCount", "categories", "faqPublishers")}
  if args.importData:
    # 匯入只操作本機資料，不應要求金鑰或載入重排序模型；不寫回使用者設定。
    localSettings = loadSettings(projectPath, environ={**os.environ, "RAG_PROVIDER": "extractive", "RAG_RETRIEVAL": "bm25", "OPENAI_API_KEY": "", "RAG_RERANKER_PATH": ""})
    runtime = buildRuntime(projectPath, localSettings)
    backupPath = localSettings.dataDir / "backups" / ("knowledge-before-120-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".db")
    backupPath.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(runtime.store.path) as source, sqlite3.connect(backupPath) as backup:
      source.backup(backup)
    runtime.store.upsert(chunks)
    for documentId in {item.documentId for item in loadChunks(projectPath / "data/seed/official_faq.jsonl")}:
      runtime.store.setActive(documentId, False)
    active = runtime.store.listChunks()
    result.update({"activeDocuments": len({item.documentId for item in active}), "activeChunks": len(active), "backupPath": str(backupPath)})
    if result["activeDocuments"] != 120:
      raise ValueError("資料庫另有其他資料，已保留；請核對文件數量")
  print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()
