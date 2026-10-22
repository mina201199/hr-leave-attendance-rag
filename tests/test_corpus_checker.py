import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def testResumeRejectsChangedCasesAtSamePath(tmp_path, monkeypatch):
  scriptPath = Path(__file__).resolve().parents[1] / "scripts/check_corpus.py"
  spec = importlib.util.spec_from_file_location("corpusChecker", scriptPath)
  checker = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(checker)
  monkeypatch.setattr(checker, "projectPath", tmp_path)
  (tmp_path / "reports").mkdir()
  casePath = tmp_path / "cases.jsonl"
  oldContents = b'{"id":"annual","question":"old question"}\n'
  casePath.write_bytes(b'{"id":"annual","question":"changed question"}\n')
  previousPath = tmp_path / "previous.json"
  previousPath.write_text(json.dumps({
    "mode": "offline", "promptVersion": checker.PROMPT_VERSION,
    "caseFile": str(casePath.resolve()), "caseSha256": hashlib.sha256(oldContents).hexdigest(),
    "results": [{"id": "annual"}],
  }), encoding="utf-8")
  def unexpectedRuntime(*args):
    pytest.fail("題組變更時必須在載入執行環境與重用舊答案之前拒絕")
  monkeypatch.setattr(checker, "buildRuntime", unexpectedRuntime)
  monkeypatch.setattr(sys, "argv", [str(scriptPath), "--cases", str(casePath), "--resume", str(previousPath)])
  with pytest.raises(ValueError, match="提示版本或題組不同"):
    checker.main()
