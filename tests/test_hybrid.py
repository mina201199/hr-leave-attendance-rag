from dataclasses import replace

import pytest

from labor_rag.context import buildContext
from labor_rag.hybrid import HybridRetriever, fuseRanks
from labor_rag.models import SearchHit
from test_pipeline import makeChunk


class FixedEmbedder:
  modelId = "test-vectors-only"

  def embed(self, texts):
    return [[1.0, 0.0] for text in texts]


def testHybridSemanticRecallAndFilters():
  public = makeChunk("public", text="雇主需支付報酬")
  private = replace(makeChunk("private"), allowedRoles=("staff",))
  inactive = replace(makeChunk("inactive"), active=False)
  expired = replace(makeChunk("expired"), effectiveTo="2000-01-01")
  retriever = HybridRetriever(
    [public, private, inactive, expired], FixedEmbedder(),
    {item.chunkId: [1.0, 0.0] for item in [public, private, inactive, expired]},
    accessRole="public", asOf="2026-10-21",
  )
  assert [hit.chunk.chunkId for hit in retriever.search("工作酬勞")] == ["public"]


def testRerankerNeverReceivesPrivateDocuments():
  class CheckingReranker:
    def rerank(self, question, hits):
      assert all(hit.chunk.chunkId == "public" for hit in hits)
      return hits

  retriever = HybridRetriever(
    [makeChunk("public"), replace(makeChunk("secret"), allowedRoles=("staff",))],
    reranker=CheckingReranker(),
  )
  assert len(retriever.search("扣薪")) == 1


def testRrfRewardsAgreementWithoutComparingRawScores():
  a, b = makeChunk("a"), makeChunk("b")
  hits = fuseRanks([[SearchHit(a, 9999), SearchHit(b, 1)], [SearchHit(b, 0.9)]])
  assert hits[0].chunk.chunkId == "b"
  assert hits[0].score == pytest.approx(1 / 62 + 1 / 61)


def testVectorDimensionsCannotSilentlyTruncate():
  retriever = HybridRetriever([makeChunk()], FixedEmbedder(), {"wages": [1.0]})
  with pytest.raises(ValueError, match="維度"):
    retriever.search("扣薪")


def testContextDeduplicatesParentsAndKeepsWholeConditions():
  first = replace(makeChunk("first"), parentId="p", parentText="完整條文與例外。")
  second = replace(makeChunk("second"), parentId="p", parentText="完整條文與例外。")
  hits = buildContext([SearchHit(first, 2), SearchHit(second, 1)], maxChars=1000)
  assert len(hits) == 1
  assert hits[0].chunk.text == "完整條文與例外。"
  assert buildContext([SearchHit(first, 2)], maxChars=3) == []
