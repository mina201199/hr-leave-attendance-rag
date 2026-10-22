import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.providers import OpenAIClient, PROMPT_VERSION
from labor_rag.runtime import buildRuntime, indexMissing
from labor_rag.settings import loadSettings


def saveJsonReport(path, data):
  temporary = path.with_suffix(".tmp")
  temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
  for attempt in range(5):
    try:
      temporary.replace(path)
      return
    except PermissionError:
      if attempt == 4:
        raise
      # Windows 的掃描器或檢視工具可能短暫持有檔案，只重試儲存，不重送 API。
      time.sleep(0.1 * (attempt + 1))


def main():
  parser = argparse.ArgumentParser(description="120 筆語料驗證；預設離線，--live 才會付費")
  parser.add_argument("--live", action="store_true")
  parser.add_argument("--resume", type=Path, help="沿用先前報告已完成題目，不重送其 API")
  parser.add_argument("--cases", type=Path, default=projectPath / "data/evaluation/corpus-120.jsonl", help="指定回歸題組 JSONL，沿用相同費用上限")
  args = parser.parse_args()
  caseBytes = args.cases.read_bytes()
  mode = "live" if args.live else "offline"
  reportPath = projectPath / "reports" / (f"corpus-check-{mode}-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
  historicalReservation = 0
  for pattern in ("live-api-smoke-*.json", "corpus-check-live-*.json"):
    for path in (projectPath / "reports").glob(pattern):
      previous = json.loads(path.read_text(encoding="utf-8"))
      historicalReservation += sum(call.get("reservedUsd", 0) for call in previous.get("calls", []))
  report = {"mode": mode, "promptVersion": PROMPT_VERSION, "caseFile": str(args.cases.resolve()), "budgetUsd": 3, "historicalReservationUsd": historicalReservation,
    "caseSha256": hashlib.sha256(caseBytes).hexdigest(),
    "calls": [], "results": [], "costNote": "依 2026-10-21 公開標準價格與 API 用量保守估算，未折抵輸入快取；非帳戶帳單或全站預算鎖。每次請求先預留 US$0.05，含先前測試預留總額不超過 US$3。",
    "pricingSources": ["https://developers.openai.com/api/docs/models/gpt-6-luna", "https://developers.openai.com/api/docs/models/text-embedding-3-small"]}
  if args.resume:
    previous = json.loads(args.resume.read_text(encoding="utf-8"))
    if previous["mode"] != mode:
      raise ValueError("接續報告模式不一致")
    if (previous.get("promptVersion"), previous.get("caseFile"), previous.get("caseSha256")) != (PROMPT_VERSION, report["caseFile"], report["caseSha256"]):
      raise ValueError("提示版本或題組不同，請重新測試，不沿用舊答案")
    report["results"] = previous["results"]
    report["resumedFrom"] = str(args.resume.resolve())
  def saveReport():
    report["estimatedCostUsd"] = sum(call.get("estimatedCostUsd", 0) for call in report["calls"])
    report["reservedTotalUsd"] = sum(call["reservedUsd"] for call in report["calls"])
    saveJsonReport(reportPath, report)

  originalPost = OpenAIClient.post
  def auditedPost(client, endpoint, payload):
    reservation = historicalReservation + (len(report["calls"]) + 1) * 0.05
    if reservation > 3 or len(report["calls"]) >= 50:
      raise ValueError("測試費用預留上限已達，不再發送請求")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 100000:
      raise ValueError("測試輸入超過保守計費預算")
    if endpoint == "responses":
      if payload.get("model") != "gpt-6-luna" or payload.get("max_output_tokens", 0) != 3000:
        raise ValueError("模型或輸出上限與核准設定不同")
    elif endpoint != "embeddings" or payload.get("model") != "text-embedding-3-small":
      raise ValueError("非核准的 API 或 embedding 模型")
    call = {"endpoint": endpoint, "model": payload["model"], "reservedUsd": 0.05, "status": "started"}
    report["calls"].append(call)
    saveReport()
    try:
      result = originalPost(client, endpoint, payload)
      usage = result.get("usage", {})
      call.update(status="returned", usage=usage)
      if endpoint == "embeddings":
        call["estimatedCostUsd"] = usage.get("total_tokens", 0) * 0.02 / 1000000
      else:
        cacheWrites = usage.get("input_tokens_details", {}).get("cache_write_tokens", 0)
        call["estimatedCostUsd"] = (usage.get("input_tokens", 0) * 0.10 + cacheWrites * 0.025 + usage.get("output_tokens", 0) * 0.50) / 1000000
      return result
    except Exception as error:
      call.update(status="failed", errorType=type(error).__name__)
      raise
    finally:
      saveReport()

  settings = None
  try:
    settings = loadSettings(projectPath) if args.live else loadSettings(projectPath,
      environ={**os.environ, "RAG_PROVIDER": "extractive", "RAG_RETRIEVAL": "bm25", "OPENAI_API_KEY": "", "RAG_RERANKER_PATH": ""})
    if args.live:
      if (settings.chatModel, settings.embeddingModel, settings.provider, settings.retrievalMode, settings.rerankerPath) != ("gpt-6-luna", "text-embedding-3-small", "openai", "hybrid", ""):
        raise ValueError("設定與核准模型不一致")
      OpenAIClient.post = auditedPost
    runtime = buildRuntime(projectPath, settings)
    if len({item.documentId for item in runtime.chunks}) != 120:
      raise ValueError("請先匯入 120 筆語料")
    if args.live:
      report["index"] = indexMissing(runtime.store, runtime.embedder)
      runtime = buildRuntime(projectPath, settings)
      report["missingVectorsAfter"] = runtime.missingVectors
      print(json.dumps({"index": report["index"]}), flush=True)
    cases = [json.loads(line) for line in caseBytes.decode("utf-8").splitlines() if line.strip()]
    completed = {item["id"] for item in report["results"]}
    for case in cases:
      if case["id"] in completed:
        continue
      answer = runtime.service.ask(case["question"], category=case["category"], companyName=case["companyName"])
      retrieved = [hit.chunk.documentId for hit in answer.hits]
      cited = [item.documentId for item in answer.citations]
      relevant = set(case["relevantDocumentIds"])
      result = {**case, "retrievedDocumentIds": retrieved, "citedDocumentIds": cited,
        "expectedSourceHit": bool(relevant.intersection(retrieved)) if relevant else None,
        "expectedSourceCited": bool(relevant.intersection(cited)) if relevant else None,
        "statusMatches": answer.status == case["expectedStatus"], "answer": asdict(answer), "manualReview": None}
      report["results"].append(result)
      saveReport()
      print(json.dumps({"id": case["id"], "status": answer.status, "expectedSourceHit": result["expectedSourceHit"], "expectedSourceCited": result["expectedSourceCited"]}), flush=True)
    answerable = [item for item in report["results"] if item["relevantDocumentIds"]]
    report["summary"] = {"caseCount": len(cases), "statusMatches": sum(item["statusMatches"] for item in report["results"]),
      "answerableCount": len(answerable), "sourceHitsAt3": sum(item["expectedSourceHit"] for item in answerable),
      "expectedSourcesCited": sum(item["expectedSourceCited"] for item in answerable)}
  except Exception as error:
    message = str(error)
    if settings and settings.apiKey:
      message = message.replace(settings.apiKey, "[REDACTED]")
    report["error"] = {"type": type(error).__name__, "message": message[:300]}
    print(json.dumps(report["error"], ensure_ascii=False), flush=True)
  finally:
    OpenAIClient.post = originalPost
    saveReport()
    print(json.dumps({"reportPath": str(reportPath), "summary": report.get("summary"), "estimatedCostUsd": report["estimatedCostUsd"]}, ensure_ascii=False), flush=True)
  if "error" in report:
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
