import pytest

from labor_rag.evaluation import evaluateCases
from labor_rag.retrieval import Retriever
from test_pipeline import makeChunk


def testRecallUsesRelevantDocumentDenominatorAndSeparatesAbstentions():
  retriever = Retriever([makeChunk("wages"), makeChunk("leave", "休假", "特別休假年資")])
  cases = [
    {"id": "one", "question": "扣薪", "relevantIds": ["wages"]},
    {"id": "two", "question": "扣薪", "relevantIds": ["wages", "leave"]},
    {"id": "none", "question": "火星火山", "relevantIds": []},
  ]
  result = evaluateCases(retriever, cases, topK=1)
  assert result["meanRecallAtK"] == pytest.approx(0.75)
  assert result["noEvidenceAccuracy"] == 1.0
  assert result["answerableCount"] == 2
  assert result["noEvidenceCount"] == 1


def testUnknownGoldIdRejected():
  with pytest.raises(ValueError, match="不存在"):
    evaluateCases(Retriever([]), [{"id": "bad", "question": "扣薪", "relevantIds": ["missing"]}])


def testEmptyEvaluationDoesNotPretendPerfectScore():
  result = evaluateCases(Retriever([]), [])
  assert result["meanRecallAtK"] is None
  assert result["noEvidenceAccuracy"] is None


def testPrecisionUsesKAndMrrUsesFirstRelevantRank():
  from labor_rag.models import SearchHit
  class FixedRetriever:
    chunks = [makeChunk("wrong"), makeChunk("right")]

    def search(self, question, topK):
      return [SearchHit(self.chunks[0], 2), SearchHit(self.chunks[1], 1)]

  result = evaluateCases(FixedRetriever(), [{"id": "one", "question": "扣薪", "relevantIds": ["right"]}], topK=3)
  assert result["meanPrecisionAtK"] == pytest.approx(1 / 3)
  assert result["mrr"] == 0.5
  assert result["details"][0]["elapsedMs"] >= 0
