import json

import pytest

from labor_rag.ingestion import loadChunks, parsePenaltyCsv
from labor_rag.models import DocumentChunk, GeneratedAnswer
from labor_rag.retrieval import Retriever
from labor_rag.service import RagService


def makeChunk(chunkId="wages", category="薪資", text="雇主不得因員工損壞物品逕自扣發工資。"):
  return DocumentChunk(
    chunkId=chunkId, documentId=chunkId, title="打破物品與扣薪",
    text=text, sourceUrl="https://bola.gov.taipei/example",
    publisher="測試資料", category=category, sourceType="官方摘要",
    collectedAt="2026-10-21", sourceUpdatedAt=None,
  )


def testSearchFindsWagesForColloquialQuestion():
  retriever = Retriever([makeChunk(), makeChunk("leave", "休假", "工讀生符合條件也有特別休假。")])
  hits = retriever.search("老闆可以扣薪嗎？")
  assert hits[0].chunk.chunkId == "wages"


def testSearchDoesNotLeakOtherCategories():
  retriever = Retriever([makeChunk()])
  assert retriever.search("扣薪", category="休假") == []


def testUnknownQuestionReturnsNoEvidence():
  result = RagService(Retriever([makeChunk()])).ask("火星火山地質")
  assert result.status == "no_evidence"
  assert result.citations == []


@pytest.mark.parametrize("question", ["", "   ", "薪" * 1001])
def testInvalidQuestionRejected(question):
  with pytest.raises(ValueError):
    RagService(Retriever([makeChunk()])).ask(question)


def testInvalidTopKRejected():
  with pytest.raises(ValueError):
    Retriever([makeChunk()]).search("扣薪", topK=0)


def testExtractedAnswerMatchesCitedSource():
  source = makeChunk()
  result = RagService(Retriever([source])).ask("扣薪")
  assert result.status == "answered"
  assert source.text in result.text
  assert [item.chunkId for item in result.citations] == [source.chunkId]
  assert result.mode == "來源摘錄"


def testFabricatedCitationIsRejected():
  class InvalidGenerator:
    mode = "測試"

    def generate(self, question, hits):
      return GeneratedAnswer(text="不應顯示", citationIds=["invented"])

  with pytest.raises(ValueError, match="引用"):
    RagService(Retriever([makeChunk()]), InvalidGenerator()).ask("扣薪")


def testNoEvidenceNeverCallsGenerator():
  class UnexpectedGenerator:
    mode = "測試"

    def generate(self, question, hits):
      pytest.fail("查無證據時不可呼叫模型")

  assert RagService(Retriever([]), UnexpectedGenerator()).ask("扣薪").status == "no_evidence"


def testLoadRejectsDuplicateIds(tmp_path):
  from dataclasses import asdict
  record = json.dumps(asdict(makeChunk()), ensure_ascii=False)
  sourcePath = tmp_path / "duplicate.jsonl"
  sourcePath.write_text(record + "\n" + record, encoding="utf-8")
  with pytest.raises(ValueError, match="重複"):
    loadChunks(sourcePath)


def testPenaltyImportPreservesOfficialFieldsAndExactCompanyFilter():
  csvText = (
    "\ufeff公告日期,處分日期,處分字號,事業單位或事業主之名稱,違反勞動基準法條款,違反法規內容,罰鍰金額,備註\n"
    "1150101,1141220,測試001,虛構甲公司,第24條,未給付加班費,20000,僅供測試\n"
    "1150101,1141221,測試002,虛構甲公司分公司,第24條,未給付加班費,40000,僅供測試\n"
  )
  chunks = parsePenaltyCsv(csvText)
  assert len(chunks) == 2
  assert "1141220" in chunks[0].text
  assert "20000" in chunks[0].text
  hits = Retriever(chunks).search("加班費", companyName="虛構甲公司")
  assert len(hits) == 1
  assert hits[0].chunk.companyName == "虛構甲公司"


def testPenaltyCompanyLookupCanReturnRecordsWithoutTextMatch():
  chunks = parsePenaltyCsv("事業單位名稱,違反法規內容\n虛構乙公司,未給付加班費\n")
  hits = Retriever(chunks).search("裁罰紀錄", companyName="虛構乙公司")
  assert len(hits) == 1


def testMalformedPenaltyFileRejected():
  with pytest.raises(ValueError, match="欄位"):
    parsePenaltyCsv("姓名,分數\n測試,100\n")


def testCompanyNameAloneCanQueryPenaltyRecords():
  chunks = parsePenaltyCsv("事業單位名稱,違反法規內容\n虛構乙公司,未給付加班費\n")
  result = RagService(Retriever(chunks)).ask("  ", companyName=" 虛構乙公司 ")
  assert result.status == "answered"
  assert result.citations[0].companyName == "虛構乙公司"


def testBlankCompanyDoesNotBypassEmptyQuestionValidation():
  with pytest.raises(ValueError):
    RagService(Retriever([])).ask("  ", companyName="  ")


def testOversizedCsvCellProducesReadableValidationError():
  import csv
  csvText = "事業單位名稱,違反法規內容\n虛構乙公司," + "x" * (csv.field_size_limit() + 1)
  with pytest.raises(ValueError, match="CSV"):
    parsePenaltyCsv(csvText)


def testUnsafeSourceUrlRejected(tmp_path):
  from dataclasses import asdict
  record = asdict(makeChunk())
  record["sourceUrl"] = "javascript:alert(1)"
  sourcePath = tmp_path / "unsafe.jsonl"
  sourcePath.write_text(json.dumps(record), encoding="utf-8")
  with pytest.raises(ValueError, match="網址"):
    loadChunks(sourcePath)
