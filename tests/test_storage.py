from dataclasses import replace

import pytest

from labor_rag.storage import KnowledgeStore
from labor_rag.processing import splitChunk
from test_pipeline import makeChunk


def testVersionReplacementAndArchiveSurviveReopen(tmp_path):
  path = tmp_path / "knowledge.db"
  store = KnowledgeStore(path)
  old = replace(makeChunk(), version="v1")
  new = replace(old, version="v2", text="新版薪資規則")
  store.upsert([old])
  store.upsert([new])
  assert [item.text for item in KnowledgeStore(path).listChunks()] == ["新版薪資規則"]
  assert len(store.listChunks(includeInactive=True)) == 2
  store.setActive(new.documentId, False)
  store.upsert([new])
  assert store.listChunks() == []


def testChangedContentRequiresNewVersionAndRollsBack(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  original = makeChunk()
  store.upsert([original])
  with pytest.raises(ValueError, match="版本"):
    store.upsert([makeChunk("another"), replace(original, text="改過的內容")])
  assert [item.chunkId for item in store.listChunks()] == ["wages"]


def testVectorsAreBoundToModelContentAndVersion(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  chunk = makeChunk()
  store.upsert([chunk])
  store.saveVectors("model-a", [chunk], [[1.0, 0.0]])
  assert store.loadVectors("model-a", [chunk]) == {"wages": [1.0, 0.0]}
  assert store.loadVectors("model-b", [chunk]) == {}
  assert store.loadVectors("model-a", [replace(chunk, text="不同內容")]) == {}
  assert store.loadVectors("model-a", [replace(chunk, version="v2")]) == {}


def testMalformedVectorsNeverEnterIndex(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  for vectors in ([[float("nan")]], [[0.0, 0.0]], []):
    with pytest.raises(ValueError):
      store.saveVectors("model", [makeChunk()], vectors)


def testSplittingPreservesSentenceAndParentContext():
  text = "工資應依法給付。\n\n雇主不可單方面認定賠償再扣薪。\n\n若無法達成協議，應循司法途徑處理。"
  chunks = splitChunk(makeChunk(text=text), maxChars=25, overlapChars=5)
  assert len(chunks) > 1
  assert all(item.sourceUrl == makeChunk().sourceUrl for item in chunks)
  assert all(item.parentText == text for item in chunks)
  assert any("若無法達成協議，應循司法途徑處理。" in item.text for item in chunks)


def testOversizedSentenceIsNotCutMidCondition():
  text = "若" + "條件" * 50 + "則依法處理。"
  chunks = splitChunk(makeChunk(text=text), maxChars=30, overlapChars=5)
  assert chunks[0].text == text


def testReimportUnchangedRecordOnAnotherDayKeepsOriginalCollectedDate(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  source = makeChunk()
  store.upsert([source])
  store.upsert([replace(source, collectedAt="2026-10-22")])
  assert len(store.listChunks(includeInactive=True)) == 1
  assert store.listChunks()[0].collectedAt == source.collectedAt


def testFutureRevisionDoesNotHideCurrentlyEffectiveVersion(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  old = replace(makeChunk(), version="v1", effectiveFrom="2026-01-01", effectiveTo="2026-12-31")
  new = replace(old, version="v2", text="未來規定", effectiveFrom="2027-01-01", effectiveTo=None)
  store.upsert([old])
  store.upsert([new])
  assert store.listChunks(asOf="2026-10-21")[0].version == "v1"
  assert store.listChunks(asOf="2027-01-01")[0].version == "v2"


def testExpiredReplacementDoesNotReviveSupersededVersion(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  store.upsert([makeChunk()])
  store.upsert([replace(makeChunk(), version="v2", effectiveFrom="2026-01-01", effectiveTo="2026-02-01")])
  assert store.listChunks(asOf="2026-10-21") == []
