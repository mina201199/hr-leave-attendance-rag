from dataclasses import replace

import pytest

from labor_rag.runtime import buildRuntime, indexMissing
from labor_rag.settings import Settings
from labor_rag.storage import KnowledgeStore
from test_pipeline import makeChunk


def testIndexingOnlySendsPublicCurrentRecordsAndResumes(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  store.upsert([makeChunk(), replace(makeChunk("secret"), allowedRoles=("staff",))])
  class RecordingEmbedder:
    modelId = "test-model"
    lastUsage = {}

    def embed(self, texts):
      assert len(texts) == 1
      return [[1.0, 0.0]]

  assert indexMissing(store, RecordingEmbedder())["indexed"] == 1
  assert indexMissing(store, RecordingEmbedder())["indexed"] == 0


def testRuntimePersistsAndDoesNotReactivateArchivedSeed(tmp_path):
  from pathlib import Path
  root = Path(__file__).resolve().parents[1]
  settings = Settings(dataDir=tmp_path)
  runtime = buildRuntime(root, settings)
  chunk = runtime.chunks[0]
  runtime.store.setActive(chunk.documentId, False)
  reopened = buildRuntime(root, settings)
  assert all(item.documentId != chunk.documentId for item in reopened.chunks)
  assert reopened.retriever.mode == "BM25 關鍵字"


def testMissingHybridIndexFailsClearlyInsteadOfSwitchingModes(tmp_path):
  from pathlib import Path
  settings = Settings(dataDir=tmp_path, retrievalMode="hybrid", apiKey="test-key")
  runtime = buildRuntime(Path(__file__).resolve().parents[1], settings)
  assert runtime.missingVectors == 6
  with pytest.raises(ValueError, match="索引"):
    runtime.service.ask("扣薪")


def testCloudPipelineIndexesRetrievesGeneratesAndRecordsUsage(tmp_path, monkeypatch):
  import json
  from pathlib import Path
  import httpx
  from labor_rag.providers import OpenAIClient

  def handler(request):
    body = json.loads(request.content)
    if request.url.path.endswith("embeddings"):
      return httpx.Response(200, json={"data": [{"index": index, "embedding": [1.0, 0.0] if "打破" in text else [0.0, 1.0]} for index, text in enumerate(body["input"])], "usage": {"total_tokens": len(body["input"])}})
    evidence = json.loads(body["input"])["evidence"]
    answer = {"answerable": True, "claims": [{"text": "測試來源回答", "citationIds": [evidence[0]["chunkId"]]}]}
    return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}], "usage": {"input_tokens": 20, "output_tokens": 10}})

  client = OpenAIClient("fake", client=httpx.Client(transport=httpx.MockTransport(handler)))
  monkeypatch.setattr("labor_rag.runtime.OpenAIClient", lambda *args: client)
  settings = Settings(dataDir=tmp_path, retrievalMode="hybrid", provider="openai", apiKey="fake", chatModel="mock-model")
  root = Path(__file__).resolve().parents[1]
  runtime = buildRuntime(root, settings)
  assert indexMissing(runtime.store, runtime.embedder)["indexed"] == 6
  reopened = buildRuntime(root, settings)
  assert reopened.missingVectors == 0
  result = reopened.service.ask("打破杯子老闆可以扣薪嗎？")
  assert result.status == "answered"
  assert result.citations[0].chunkId == "faq-wages-damage"
  trace = reopened.traces.recent()[0]
  assert trace["embeddingUsage"]["total_tokens"] == 1
  assert trace["generationUsage"]["input_tokens"] == 20
