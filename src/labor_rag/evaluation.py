from time import perf_counter

from .retrieval import Retriever


def evaluateCases(retriever: Retriever, cases: list[dict], topK: int = 3) -> dict:
  if not 1 <= topK <= 10:
    raise ValueError("topK 必須介於 1 與 10")
  knownIds = {chunk.chunkId for chunk in retriever.chunks}
  recalls = []
  precisions = []
  reciprocalRanks = []
  noEvidenceResults = []
  details = []
  for case in cases:
    relevantIds = set(case["relevantIds"])
    if not relevantIds.issubset(knownIds):
      raise ValueError(f"題目 {case['id']} 指向不存在的片段")
    started = perf_counter()
    hits = retriever.search(case["question"], topK=topK)
    elapsedMs = round((perf_counter() - started) * 1000, 2)
    retrievedIds = [hit.chunk.chunkId for hit in hits]
    recall = len(relevantIds.intersection(retrievedIds)) / len(relevantIds) if relevantIds else None
    if recall is not None:
      recalls.append(recall)
      precisions.append(len(relevantIds.intersection(retrievedIds)) / topK)
      reciprocalRanks.append(next((1 / rank for rank, chunkId in enumerate(retrievedIds, 1) if chunkId in relevantIds), 0))
    else:
      noEvidenceResults.append(not hits)
    details.append({"id": case["id"], "question": case["question"], "relevantIds": sorted(relevantIds), "retrievedIds": retrievedIds, "recallAtK": recall,
      "precisionAtK": precisions[-1] if recall is not None else None, "reciprocalRank": reciprocalRanks[-1] if recall is not None else None, "elapsedMs": elapsedMs})
  return {
    "topK": topK,
    "answerableCount": len(recalls),
    "noEvidenceCount": len(noEvidenceResults),
    "meanRecallAtK": sum(recalls) / len(recalls) if recalls else None,
    "meanPrecisionAtK": sum(precisions) / len(precisions) if precisions else None,
    "mrr": sum(reciprocalRanks) / len(reciprocalRanks) if reciprocalRanks else None,
    "meanRetrievalMs": sum(item["elapsedMs"] for item in details) / len(details) if details else None,
    "noEvidenceAccuracy": sum(noEvidenceResults) / len(noEvidenceResults) if noEvidenceResults else None,
    "details": details,
  }
