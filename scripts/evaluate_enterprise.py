import argparse
import json
import sys
from pathlib import Path

projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.enterprise_evaluation import runEvaluation


def main(argv=None):
  parser = argparse.ArgumentParser(description="企業版離線虛構語料評估；不讀取 .env、不呼叫外部模型")
  parser.add_argument("--cases", type=Path, default=projectPath / "data/evaluation/enterprise-development.jsonl")
  parser.add_argument("--corpus", type=Path, default=projectPath / "data/company_demo/knowledge.jsonl")
  parser.add_argument("--output", type=Path)
  args = parser.parse_args(argv)
  result = runEvaluation(args.cases, args.corpus)
  text = json.dumps(result, ensure_ascii=False, indent=2)
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text + "\n", encoding="utf-8")
  print(text)
  return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
  raise SystemExit(main())
