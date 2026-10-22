import argparse
import json
import sys
from pathlib import Path


projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.ingestion import loadChunks, parsePenaltyCsv
from labor_rag.processing import splitChunk
from labor_rag.runtime import buildRuntime, indexMissing
from labor_rag.settings import loadSettings


def main():
  parser = argparse.ArgumentParser(description="本機知識庫維護")
  commands = parser.add_subparsers(dest="command", required=True)
  commands.add_parser("status")
  commands.add_parser("index")
  importCommand = commands.add_parser("import")
  importCommand.add_argument("path", type=Path)
  for name in ("deactivate", "activate"):
    command = commands.add_parser(name)
    command.add_argument("documentId")
  arguments = parser.parse_args()
  try:
    runtime = buildRuntime(projectPath, loadSettings(projectPath))
    if arguments.command == "import":
      chunks = parsePenaltyCsv(arguments.path.read_text(encoding="utf-8-sig")) if arguments.path.suffix.lower() == ".csv" else loadChunks(arguments.path)
      chunks = [part for chunk in chunks for part in splitChunk(chunk)]
      runtime.store.upsert(chunks)
      print(f"已處理 {len(chunks)} 個片段，相同裁罰案件與內容不重複儲存；若使用混合檢索，請執行 index。")
    elif arguments.command == "index":
      if runtime.embedder is None:
        raise ValueError("請先在 .env 設定 OPENAI_API_KEY 與 embedding 模型")
      print(json.dumps(indexMissing(runtime.store, runtime.embedder), ensure_ascii=False, indent=2))
    elif arguments.command in ("activate", "deactivate"):
      runtime.store.setActive(arguments.documentId, arguments.command == "activate")
      print("已更新文件啟用狀態；歷史版本保留。")
    else:
      print(json.dumps({"publicChunks": len(runtime.chunks), "allRevisionChunks": len(runtime.store.listChunks(includeInactive=True)),
        "retrievalMode": runtime.retriever.mode, "answerMode": runtime.service.generator.mode, "missingVectors": runtime.missingVectors}, ensure_ascii=False, indent=2))
  except (ValueError, OSError) as error:
    parser.exit(1, f"操作失敗：{error}\n")


if __name__ == "__main__":
  main()
