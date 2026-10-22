import json

import httpx
import pytest

from labor_rag.enterprise_generation import EnterpriseOpenAIGenerator
from labor_rag.models import DocumentChunk, SearchHit
from labor_rag.providers import OpenAIClient


def evidence():
  return [SearchHit(DocumentChunk("policy", "policy", "【虛構示範】流程", "【虛構示範】請提供起訖與說明。",
    "internal://policy", "虛構公司", "休假", "公司制度", "2026-10-22", allowedRoles=("employee",),
    effectiveFrom="2026-01-01", maintainerId="demo", isDemo=True, externalProcessingAllowed=True), 1)]


def generatorFor(answer, payloads=None):
  def handler(request):
    if payloads is not None:
      payloads.append(json.loads(request.content))
    return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
      {"type": "output_text", "text": json.dumps(answer, ensure_ascii=False)}]}], "usage": {"output_tokens": 10}})
  client = OpenAIClient("synthetic-http-mock", client=httpx.Client(transport=httpx.MockTransport(handler)))
  return EnterpriseOpenAIGenerator(client, "gpt-6-luna")


@pytest.mark.parametrize("status,claims", [
  ("answered", [{"text": "請提供起訖與說明。", "citationIds": ["policy"]}]),
  ("no_evidence", []), ("needs_clarification", []),
  ("conflict", [{"text": "適用條件待確認。", "citationIds": ["policy"]}]),
])
def test_structured_statuses_and_citations_are_preserved(status, claims):
  generator = generatorFor({"status": status, "claims": claims})
  answer = generator.generate("請假流程", evidence())
  assert answer.status == status
  assert [claim.text for claim in answer.claims] == [claim["text"] for claim in claims]


@pytest.mark.parametrize("answer", [
  {"status": "answered", "claims": []},
  {"status": "answered", "claims": [{"text": "無依據", "citationIds": []}]},
  {"status": "answered", "claims": [{"text": "無依據", "citationIds": ["secret"]}]},
  {"status": "answered", "claims": [{"text": "", "citationIds": ["policy"]}]},
  {"status": "no_evidence", "claims": [{"text": "矛盾內容", "citationIds": ["policy"]}]},
  {"status": "invented", "claims": []},
  {"status": "answered", "claims": [{"text": "有效", "citationIds": "policy"}]},
  {"status": "answered", "claims": [], "unvalidated": "不得顯示"},
])
def test_invalid_claims_or_unknown_citations_never_become_answer(answer):
  with pytest.raises(ValueError, match="引用|格式"):
    generatorFor(answer).generate("請假流程", evidence())


def test_enterprise_request_distinguishes_demo_and_official_and_retains_conditions():
  payloads = []
  generatorFor({"status": "no_evidence", "claims": []}, payloads).generate("問題", evidence())
  request = payloads[0]
  assert request["model"] == "gpt-6-luna"
  assert request["store"] is False
  for word in ("虛構", "官方", "個人", "表單", "期限", "核准", "條件"):
    assert word in request["instructions"]
  item = json.loads(request["input"])["evidence"][0]
  assert item["isDemo"] is True
  assert item["effectiveFrom"] == "2026-01-01"
  assert "status" in request["text"]["format"]["schema"]["required"]
