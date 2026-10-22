import json

import pytest

from labor_rag.models import GeneratedAnswer
from labor_rag.retrieval import Retriever
from labor_rag.service import RagService
from labor_rag.settings import loadSettings
from labor_rag.telemetry import TraceStore
from test_pipeline import makeChunk


def testMissingKeyKeepsOfflineMode(tmp_path):
  settings = loadSettings(tmp_path, environ={})
  assert settings.provider == "extractive"
  assert not settings.apiKey


def testCloudSelectionRequiresKeyAndExplicitModel(tmp_path):
  with pytest.raises(ValueError, match="OPENAI_API_KEY"):
    loadSettings(tmp_path, environ={"RAG_PROVIDER": "openai"})
  with pytest.raises(ValueError, match="OPENAI_CHAT_MODEL"):
    loadSettings(tmp_path, environ={"RAG_PROVIDER": "openai", "OPENAI_API_KEY": "fake"})


def testTraceDoesNotStoreQuestionOrAnswerAndFeedbackPersists(tmp_path):
  traces = TraceStore(tmp_path / "traces.db")
  result = RagService(Retriever([makeChunk()]), traceStore=traces).ask("老闆可以扣薪嗎？")
  record = traces.recent()[0]
  assert result.traceId == record["traceId"]
  assert record["status"] == "answered"
  assert "老闆可以扣薪嗎" not in json.dumps(record, ensure_ascii=False)
  traces.feedback(result.traceId, "helpful")
  assert TraceStore(tmp_path / "traces.db").recent()[0]["feedback"] == "helpful"


def testRefusalIsNotPresentedAsAnswered():
  class RefusingGenerator:
    mode = "測試"

    def generate(self, question, hits):
      return GeneratedAnswer("", [], answerable=False)

  result = RagService(Retriever([makeChunk()]), RefusingGenerator()).ask("扣薪")
  assert result.status == "no_evidence"
  assert result.citations == []


def testGenerationErrorsAreRecordedWithoutRawError(tmp_path):
  class BrokenGenerator:
    mode = "測試"

    def generate(self, question, hits):
      raise ValueError("private diagnostic")

  traces = TraceStore(tmp_path / "traces.db")
  with pytest.raises(ValueError):
    RagService(Retriever([makeChunk()]), BrokenGenerator(), traceStore=traces).ask("扣薪")
  record = traces.recent()[0]
  assert record["status"] == "error"
  assert "private diagnostic" not in json.dumps(record)


def testOversizedContextReportsBudgetLimitNotMissingEvidence():
  from labor_rag.processing import splitChunk
  chunks = splitChunk(makeChunk(text="雇主不得逕自扣發工資。" * 800))
  result = RagService(Retriever(chunks)).ask("扣薪")
  assert result.status == "context_limit"
  assert len(result.hits) == 3
  assert "預算" in result.text
