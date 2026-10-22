import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.evaluation import evaluateCases
from labor_rag.ingestion import loadChunks
from labor_rag.retrieval import Retriever
from labor_rag.runtime import buildRuntime
from labor_rag.service import RagService
from labor_rag.settings import loadSettings


def main():
  parser = argparse.ArgumentParser(description="檢索評估與人工評分資料匯出")
  parser.add_argument("--mode", choices=["baseline", "configured"], default="baseline")
  parser.add_argument("--cases", type=Path, default=projectPath / "data/evaluation/smoke.jsonl")
  parser.add_argument("--top-k", type=int, default=3)
  parser.add_argument("--answers", action="store_true", help="另外產生回答及人工評分欄位；雲端模式會產生 API 費用")
  arguments = parser.parse_args()
  runtime = None
  if arguments.mode == "configured":
    settings = loadSettings(projectPath)
    runtime = buildRuntime(projectPath, settings)
    retriever, service = runtime.retriever, runtime.service
  else:
    chunks = loadChunks(projectPath / "data/seed/official_faq.jsonl")
    retriever = Retriever(chunks)
    service = RagService(retriever)
  caseText = arguments.cases.read_text(encoding="utf-8")
  lines = caseText.splitlines()
  cases = [json.loads(line) for line in lines if line.strip()]
  report = evaluateCases(retriever, cases, arguments.top_k)
  report.update(mode=arguments.mode, retrievalMode=getattr(retriever, "mode", "BM25 關鍵字"),
    createdAt=datetime.now(timezone.utc).isoformat(), casesHash=hashlib.sha256(caseText.encode("utf-8")).hexdigest(),
    embeddingModel=runtime.embedder.modelId if runtime and runtime.retriever.embedder else None,
    rerankerPath=settings.rerankerPath if runtime else None,
    minSimilarity=settings.minSimilarity if runtime else None,
    sourceVersions={chunk.chunkId: chunk.version for chunk in retriever.chunks})
  if arguments.answers:
    for case, detail in zip(cases, report["details"]):
      answer = service.ask(case["question"], arguments.top_k)
      detail.update(answer=answer.text, answerStatus=answer.status, answerMode=answer.mode,
        citationIds=[chunk.chunkId for chunk in answer.citations], endToEndMs=answer.elapsedMs,
        expectedPoints=case.get("expectedPoints", []), manualReview={"correctness": None, "faithfulness": None, "citationPrecision": None, "notes": ""})
  outputPath = projectPath / "reports"
  outputPath.mkdir(exist_ok=True)
  reportFile = outputPath / ("smoke-evaluation.json" if arguments.mode == "baseline" else "configured-evaluation.json")
  reportFile.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
  print(json.dumps({key: value for key, value in report.items() if key not in ("details", "sourceVersions")}, ensure_ascii=False, indent=2))
  print(f"Report: {reportFile}")


if __name__ == "__main__":
  main()
