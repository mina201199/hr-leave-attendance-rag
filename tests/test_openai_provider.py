import json

import httpx
import pytest

from labor_rag.models import SearchHit
from labor_rag.providers import OpenAIClient, OpenAIEmbedder, OpenAIGenerator
from test_pipeline import makeChunk


def makeClient(handler):
  return OpenAIClient("test-key-never-real", client=httpx.Client(transport=httpx.MockTransport(handler)))


def responsePayload(answer):
  return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}], "usage": {"input_tokens": 20, "output_tokens": 10}}


def testEmbeddingUsesProviderVectorsAndRestoresInputOrder():
  def handler(request):
    body = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert body["input"] == ["甲", "乙"]
    return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}], "usage": {"total_tokens": 4}})
  embedder = OpenAIEmbedder(makeClient(handler), "test-embedding")
  assert embedder.embed(["甲", "乙"]) == [[1, 0], [0, 1]]
  assert embedder.lastUsage["total_tokens"] == 4


def testGenerationSendsEvidenceAndRequiresCitedClaims():
  def handler(request):
    body = json.loads(request.content)
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert "wages" in body["input"]
    assert "不可信" in body["instructions"]
    return httpx.Response(200, json=responsePayload({"answerable": True, "claims": [{"text": "不可逕自扣薪。", "citationIds": ["wages"]}]}))
  result = OpenAIGenerator(makeClient(handler), "test-model").generate("扣薪？", [SearchHit(makeChunk(), 1)])
  assert result.citationIds == ["wages"]
  assert "[wages]" in result.text
  assert result.usage["output_tokens"] == 10


@pytest.mark.parametrize("answer", [
  {"answerable": True, "claims": [{"text": "不存在的證據", "citationIds": ["fake"]}]},
  {"answerable": True, "claims": [{"text": "無引用", "citationIds": []}]},
  {"answerable": True, "claims": []},
  {"answerable": "false", "claims": []},
])
def testInvalidCloudAnswerIsRejected(answer):
  generator = OpenAIGenerator(makeClient(lambda request: httpx.Response(200, json=responsePayload(answer))), "test-model")
  with pytest.raises(ValueError):
    generator.generate("扣薪", [SearchHit(makeChunk(), 1)])


def testModelCanRefuseDespiteRetrievalHits():
  generator = OpenAIGenerator(makeClient(lambda request: httpx.Response(200, json=responsePayload({"answerable": False, "claims": []}))), "test-model")
  assert generator.generate("不相關", [SearchHit(makeChunk(), 1)]).answerable is False


def testHttpErrorDoesNotExposeKeyOrResponseBody():
  client = makeClient(lambda request: httpx.Response(401, text="test-key-never-real provider-internal-details"))
  with pytest.raises(ValueError) as error:
    client.post("embeddings", {})
  assert "401" in str(error.value)
  assert "test-key" not in str(error.value)
  assert "provider-internal" not in str(error.value)


def testTimeoutIsReadable():
  def handler(request):
    raise httpx.ReadTimeout("sensitive body", request=request)
  with pytest.raises(ValueError, match="逾時"):
    makeClient(handler).post("responses", {})


def testIncompleteModelResponseNeverShown():
  payload = responsePayload({"answerable": True, "claims": []})
  payload["status"] = "incomplete"
  generator = OpenAIGenerator(makeClient(lambda request: httpx.Response(200, json=payload)), "test-model")
  with pytest.raises(ValueError, match="完整"):
    generator.generate("扣薪", [SearchHit(makeChunk(), 1)])


def testMalformedResponseItemsProduceReadableError():
  generator = OpenAIGenerator(makeClient(lambda request: httpx.Response(200, json={"status": "completed", "output": [123]})), "test-model")
  with pytest.raises(ValueError, match="格式"):
    generator.generate("扣薪", [SearchHit(makeChunk(), 1)])
