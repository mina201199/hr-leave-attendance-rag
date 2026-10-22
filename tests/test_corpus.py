from dataclasses import replace

import pytest

from labor_rag import ingestion


def testOfficialFaqTypeAccepted():
  from test_pipeline import makeChunk
  ingestion.validateChunk(replace(makeChunk(), sourceType="官方問答"))


def testFaqExtractionExcludesNavigationAndUsesArticleUpdateDate():
  from labor_rag.corpus import parseOfficialFaq
  html = '<nav>錯誤導覽</nav><div class="essay"><div class="p"><p>不得逕自扣發工資。</p></div></div><li>資料更新：112-12-18 17:10</li><footer>更新日期115-10-21</footer>'
  item = {"rawFile": "bola-test.html", "url": "https://bola.gov.taipei/example", "title": "扣薪", "publisher": "臺北市政府勞動局", "category": "薪資"}
  chunk = parseOfficialFaq(html, item, "2026-10-21")
  assert chunk.text == "不得逕自扣發工資。"
  assert chunk.sourceUpdatedAt == "2023-12-18"


def testMolFaqExtractionPreservesAnswerAndRejectsMissingBody():
  from labor_rag.corpus import parseOfficialFaq
  item = {"rawFile": "mol-test.html", "url": "https://www.mol.gov.tw/example", "title": "特休", "publisher": "勞動部", "category": "休假"}
  html = '<section class="cp"><ul class="publish_info_top"><li>更新日期:2025-09-01</li></ul><table><tr><th>問題</th><td>特休</td></tr><tr><th>答案</th><td>部分工時也有特休。<br>保留條件。</td></tr></table></section>'
  chunk = parseOfficialFaq(html, item, "2026-10-21")
  assert chunk.text == "部分工時也有特休。 保留條件。"
  assert chunk.sourceUpdatedAt == "2025-09-01"
  with pytest.raises(ValueError, match="正文"):
    parseOfficialFaq('<html>網站維護中</html>', item, "2026-10-21")


def testPenaltySelectionGroupsCaseAndExcludesRevokedAndIrrelevantCases():
  from labor_rag.corpus import buildPenaltyCases
  csvText = ('公告日期,處分日期,處分字號,事業單位或事業主之名稱,違反勞動基準法條款,違反法規內容,罰鍰金額,備註,\n'
    '1150915,1150831,甲001,測試甲公司,第24條,未給加班費,20000,工讀生專案,\n'
    '1150915,1150831,甲001,測試甲公司,第39條,未給假日工資,20000,工讀生專案,\n'
    '1150915,1150831,乙002,測試乙公司,第24條,未給加班費,20000,工讀生專案；原處分撤銷,\n'
    '1150915,1150830,丙003,測試丙公司,第80條,拒絕檢查,30000,工讀生專案,\n'
    '1150915,1150829,丁004,測試丁公司,第24條,未給加班費,20000,,\n')
  chunks = buildPenaltyCases(csvText, 1, "2026-10-21")
  assert len(chunks) == 1
  assert chunks[0].companyName == "測試甲公司"
  assert "未給加班費" in chunks[0].text and "未給假日工資" in chunks[0].text
  with pytest.raises(ValueError, match="不足"):
    buildPenaltyCases(csvText, 2, "2026-10-21")


def testPublishedCorpusHas120IndependentTraceableRecords():
  from pathlib import Path
  from labor_rag.ingestion import loadChunks
  from labor_rag.processing import splitChunk
  root = Path(__file__).resolve().parents[1]
  records = loadChunks(root / "data/corpus/2026-10-21/knowledge.jsonl")
  assert len(records) == len({item.documentId for item in records}) == 120
  faq = [item for item in records if item.sourceType == "官方問答"]
  cases = [item for item in records if item.sourceType == "裁罰紀錄"]
  assert len(faq) == 40 and len(cases) == 80
  assert all(item.sourceUpdatedAt for item in faq)
  assert len({item.sourceUrl for item in faq}) == 40
  assert len({item.text for item in faq}) == 40
  assert all("工讀生與部分工時" in item.text and "處分字號：" in item.text for item in cases)
  assert all("撤銷" not in item.text for item in cases)
  pieces = [part for item in records for part in splitChunk(item)]
  assert len({item.documentId for item in pieces}) == 120
  assert all(item.parentText or item.text for item in pieces)


def testCorpusImportDoesNotRequireApiKeyAndCanBeRepeated(tmp_path, monkeypatch):
  import runpy
  import sys
  from pathlib import Path
  from labor_rag.storage import KnowledgeStore
  root = Path(__file__).resolve().parents[1]
  monkeypatch.setenv("OPENAI_API_KEY", "")
  monkeypatch.setenv("RAG_PROVIDER", "openai")
  monkeypatch.setenv("RAG_RETRIEVAL", "hybrid")
  monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
  monkeypatch.setattr(sys, "argv", ["prepare_corpus.py", "--import"])
  main = runpy.run_path(str(root / "scripts/prepare_corpus.py"))["main"]
  main()
  main()
  store = KnowledgeStore(tmp_path / "knowledge.db")
  assert len({item.documentId for item in store.listChunks()}) == 120
  assert not any(item.sourceType == "官方摘要" for item in store.listChunks())
  assert any(item.sourceType == "官方摘要" for item in store.listChunks(includeInactive=True))
  assert len(list((tmp_path / "backups").glob("*.db"))) == 2


def testReportWriteRecoversFromTemporaryWindowsFileLock(tmp_path, monkeypatch):
  import json
  import runpy
  from pathlib import Path
  root = Path(__file__).resolve().parents[1]
  namespace = runpy.run_path(str(root / "scripts/check_corpus.py"))
  assert "saveJsonReport" in namespace
  originalReplace = Path.replace
  attempts = []
  def temporarilyLocked(path, target):
    attempts.append(path)
    if len(attempts) < 3:
      raise PermissionError("temporary Windows lock")
    return originalReplace(path, target)
  monkeypatch.setattr(Path, "replace", temporarilyLocked)
  destination = tmp_path / "report.json"
  namespace["saveJsonReport"](destination, {"completed": 12})
  assert json.loads(destination.read_text(encoding="utf-8")) == {"completed": 12}


@pytest.mark.parametrize("canonicalFirst", [True, False])
def testLegacyCsvAndCuratedCasesDoNotDuplicateAcrossImportOrder(tmp_path, canonicalFirst):
  from labor_rag.corpus import buildPenaltyCases
  from labor_rag.ingestion import parsePenaltyCsv
  from labor_rag.storage import KnowledgeStore
  text = ('公告日期,處分日期,處分字號,事業單位或事業主之名稱,違反勞動基準法條款,違反法規內容,罰鍰金額,備註\n'
    '1150915,1150831,測試001,測試公司,第24條,未給加班費,20000,工讀生專案\n')
  canonical = buildPenaltyCases(text, 1, "2026-10-21")
  legacy = parsePenaltyCsv(text)
  store = KnowledgeStore(tmp_path / "knowledge.db")
  first, second = (canonical, legacy) if canonicalFirst else (legacy, canonical)
  store.upsert(first)
  store.upsert(second)
  assert [item.documentId for item in store.listChunks()] == [canonical[0].documentId]
  if not canonicalFirst:
    assert any(item.documentId == legacy[0].documentId and not item.active for item in store.listChunks(includeInactive=True))
  conflicting = parsePenaltyCsv(text.replace("20000", "30000"))
  with pytest.raises(ValueError, match="同一裁罰案件"):
    store.upsert(conflicting)
  assert len(store.listChunks()) == 1
