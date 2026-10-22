import importlib.util
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from labor_rag.ingestion import loadChunks, validateChunk
from labor_rag.models import DocumentChunk
from labor_rag.storage import KnowledgeStore
from test_pipeline import makeChunk


projectPath = Path(__file__).resolve().parents[1]


def enterpriseChunk(**changes):
  values = dict(chunkId="demo-leave", documentId="demo-leave", title="【虛構示範】請假流程",
    text="【虛構示範】填寫申請說明後送交主管確認。", sourceUrl="internal://demo-leave",
    publisher="虛構展示公司", category="休假", sourceType="公司制度", collectedAt="2026-10-22",
    version="v1", allowedRoles=("employee", "manager", "hr"), effectiveFrom="2026-01-01",
    publicationStatus="published", maintainerId="demo-hr-maintainer", isDemo=True,
    externalProcessingAllowed=False)
  values.update(changes)
  return DocumentChunk(**values)


def testLegacyPublicDefaultsRemainReadable():
  source = makeChunk()
  assert source.publicationStatus == "published"
  assert source.maintainerId is None
  assert source.isDemo is False
  assert source.externalProcessingAllowed is False
  validateChunk(source)


@pytest.mark.parametrize("sourceType", ["公司制度", "主管指引", "人資SOP"])
def testEnterpriseSourcesRequireExplicitPrivateMetadata(sourceType):
  source = enterpriseChunk(sourceType=sourceType)
  validateChunk(source)
  for changes in ({"allowedRoles": ()}, {"allowedRoles": ("public",)}, {"allowedRoles": ("staff",)},
      {"allowedRoles": ("employee", "public")}, {"maintainerId": None}, {"maintainerId": " "},
      {"effectiveFrom": None}, {"sourceUrl": "https://example.com/demo-leave"},
      {"sourceUrl": "internal://another"}, {"sourceUrl": "internal://demo-leave?role=hr"}):
    with pytest.raises(ValueError):
      validateChunk(replace(source, **changes))


@pytest.mark.parametrize("changes", [{"publicationStatus": "unknown"}, {"isDemo": 1},
  {"externalProcessingAllowed": "yes"}, {"maintainerId": 123}])
def testInvalidEnterpriseFieldValuesAreRejected(changes):
  with pytest.raises(ValueError):
    validateChunk(enterpriseChunk(**changes))


def testPublicSourceCannotClaimInternalUrl():
  with pytest.raises(ValueError):
    validateChunk(replace(makeChunk(), sourceUrl="internal://wages"))


def testLifecycleAndFutureRevisionDoNotMaskCurrentPublication(tmp_path):
  path = tmp_path / "knowledge.db"
  store = KnowledgeStore(path)
  old = enterpriseChunk()
  draft = replace(old, version="v2", text="【虛構示範】新增代理人確認。", publicationStatus="draft",
    effectiveFrom="2026-11-01")
  store.upsert([old])
  store.upsert([draft])
  assert [item.version for item in store.listChunks(asOf="2026-12-01")] == ["v1"]
  store.setPublicationStatus(old.documentId, "v2", "published")
  assert [item.version for item in store.listChunks(asOf="2026-10-31")] == ["v1"]
  assert [item.version for item in store.listChunks(asOf="2026-11-01")] == ["v2"]
  store.setPublicationStatus(old.documentId, "v2", "withdrawn")
  store.upsert([draft])
  assert KnowledgeStore(path).listChunks(asOf="2026-11-01") == []
  assert [item.version for item in store.listChunks(asOf="2026-10-31")] == ["v1"]
  history = store.listChunks(includeInactive=True, asOf="2026-11-01")
  assert [(item.version, item.publicationStatus, item.active) for item in history] == [
    ("v1", "published", False), ("v2", "withdrawn", False)]
  with pytest.raises(ValueError):
    store.setPublicationStatus(old.documentId, "missing", "published")
  with pytest.raises(ValueError):
    store.setPublicationStatus(old.documentId, "v2", "invalid")


def testDraftAndExpiredRevisionsDoNotReviveOrExposeContent(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  store.upsert([enterpriseChunk(publicationStatus="draft")])
  assert store.listChunks(asOf="2026-10-22") == []
  store.setPublicationStatus("demo-leave", "v1", "published")
  store.upsert([enterpriseChunk(version="v2", effectiveFrom="2026-02-01", effectiveTo="2026-03-01")])
  assert store.listChunks(asOf="2026-10-22") == []


def testInactiveDraftCannotDisableCurrentPublishedVersion(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  store.upsert([enterpriseChunk()])
  store.upsert([enterpriseChunk(version="v2", publicationStatus="draft", active=False)])
  assert [item.version for item in store.listChunks(asOf="2026-10-22")] == ["v1"]


def testDraftCannotBeWithdrawnAndHideCurrentPublishedVersion(tmp_path):
  path = tmp_path / "knowledge.db"
  store = KnowledgeStore(path)
  store.upsert([enterpriseChunk()])
  store.upsert([enterpriseChunk(version="v2", effectiveFrom="2026-10-01", publicationStatus="draft")])
  assert [item.version for item in store.listChunks(asOf="2026-10-22")] == ["v1"]
  store.setPublicationStatus("demo-leave", "v2", "draft")
  with pytest.raises(ValueError):
    store.setPublicationStatus("demo-leave", "v2", "withdrawn")
  reopened = KnowledgeStore(path)
  assert [item.version for item in reopened.listChunks(asOf="2026-10-22")] == ["v1"]
  assert [(item.version, item.publicationStatus) for item in reopened.listChunks(includeInactive=True)] == [
    ("v1", "published"), ("v2", "draft")]


def testEarlierEffectiveVersionImportedLastCannotMaskNewerVersion(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  store.upsert([enterpriseChunk(version="v2", effectiveFrom="2026-11-01")])
  store.upsert([enterpriseChunk()])
  assert [item.version for item in store.listChunks(asOf="2026-10-31")] == ["v1"]
  assert [item.version for item in store.listChunks(asOf="2026-11-01")] == ["v2"]


def testPublicVersionsKeepLegacyImportOrderWithEarlierEffectiveDate(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  source = replace(makeChunk(), version="v1", effectiveFrom="2026-05-01")
  store.upsert([source])
  store.upsert([replace(source, version="v2", effectiveFrom="2026-01-01")])
  assert [item.version for item in store.listChunks(asOf="2026-10-22")] == ["v2"]


@pytest.mark.parametrize("initialStatus", ["published", "withdrawn"])
def testPublicationCannotReturnToDraftAndReviveOlderVersion(tmp_path, initialStatus):
  path = tmp_path / "knowledge.db"
  store = KnowledgeStore(path)
  store.upsert([enterpriseChunk()])
  draft = enterpriseChunk(version="v2", effectiveFrom="2026-11-01", publicationStatus="draft")
  store.upsert([draft])
  store.setPublicationStatus("demo-leave", "v2", "published")
  if initialStatus == "withdrawn":
    store.setPublicationStatus("demo-leave", "v2", "withdrawn")
  with pytest.raises(ValueError):
    store.setPublicationStatus("demo-leave", "v2", "draft")
  store.upsert([draft])
  history = KnowledgeStore(path).listChunks(includeInactive=True, asOf="2026-11-01")
  assert [(item.version, item.publicationStatus) for item in history] == [
    ("v1", "published"), ("v2", initialStatus)]
  assert [item.version for item in store.listChunks(asOf="2026-11-01")] == (
    ["v2"] if initialStatus == "published" else [])
  store.setPublicationStatus("demo-leave", "v2", initialStatus)
  store.setPublicationStatus("demo-leave", "v2", "published")
  assert [item.version for item in store.listChunks(asOf="2026-11-01")] == ["v2"]


def testSameVersionPayloadRemainsImmutableAndBatchRollsBack(tmp_path):
  store = KnowledgeStore(tmp_path / "knowledge.db")
  source = enterpriseChunk()
  store.upsert([source])
  for changes in ({"text": "【虛構示範】不同內容。"}, {"allowedRoles": ("hr",)},
      {"sourceType": "主管指引"}, {"publicationStatus": "draft"}):
    with pytest.raises(ValueError, match="版本"):
      store.upsert([makeChunk("another"), replace(source, **changes)])
  assert [item.documentId for item in store.listChunks()] == ["demo-leave"]


@pytest.mark.parametrize("changes", [{"sourceType": "主管指引"}, {"allowedRoles": ("hr",)},
  {"maintainerId": "other"}, {"publicationStatus": "draft"}, {"externalProcessingAllowed": True},
  {"parentText": "【虛構示範】不同父文。"}])
def testMixedDocumentOrParentMetadataFailsClosed(tmp_path, changes):
  source = enterpriseChunk(parentId="leave-parent", parentText="【虛構示範】完整流程。")
  other = replace(source, chunkId="leave-part-2", **changes)
  with pytest.raises(ValueError):
    KnowledgeStore(tmp_path / "knowledge.db").upsert([source, other])
  sourcePath = tmp_path / "upload.jsonl"
  sourcePath.write_text("\n".join(json.dumps(asdict(item), ensure_ascii=False) for item in [source, other]), encoding="utf-8")
  with pytest.raises(ValueError):
    loadChunks(sourcePath)


@pytest.mark.parametrize("otherVersion", ["v1", "v2"])
def testParentCannotBridgeDocumentsWithDifferentAcl(tmp_path, otherVersion):
  source = enterpriseChunk(parentId="shared-parent", parentText="【虛構示範】完整流程。")
  other = replace(source, chunkId="hr-part", documentId="demo-hr", sourceUrl="internal://demo-hr", allowedRoles=("hr",), version=otherVersion)
  store = KnowledgeStore(tmp_path / "knowledge.db")
  store.upsert([source])
  with pytest.raises(ValueError):
    store.upsert([other])


def testOldPayloadMigrationPreservesHistoryVectorsAndIdempotentImport(tmp_path):
  path = tmp_path / "legacy.db"
  source = makeChunk()
  payload = asdict(source)
  for fieldName in ("publicationStatus", "maintainerId", "isDemo", "externalProcessingAllowed"):
    payload.pop(fieldName, None)
  originalPayload = json.dumps([payload])
  with sqlite3.connect(path) as connection:
    connection.executescript("CREATE TABLE documents (documentId TEXT PRIMARY KEY, version TEXT, active INTEGER);"
      "CREATE TABLE revisions (documentId TEXT, version TEXT, payload TEXT, PRIMARY KEY(documentId, version));")
    connection.execute("INSERT INTO documents VALUES (?, ?, 1)", (source.documentId, source.version))
    connection.execute("INSERT INTO revisions VALUES (?, ?, ?)", (source.documentId, source.version, originalPayload))
  store = KnowledgeStore(path)
  store.saveVectors("offline", [source], [[1, 0]])
  store.upsert([replace(source, collectedAt="2026-10-22")])
  store.setPublicationStatus(source.documentId, source.version, "withdrawn")
  reopened = KnowledgeStore(path)
  reopened.upsert([source])
  assert reopened.listChunks() == []
  assert reopened.listChunks(includeInactive=True)[0].collectedAt == "2026-10-21"
  assert reopened.loadVectors("offline", [source]) == {"wages": [1, 0]}
  with sqlite3.connect(path) as connection:
    assert connection.execute("SELECT payload FROM revisions").fetchone()[0] == originalPayload


def testDemoImportUsesSeparatePathAndPreservesLifecycle(tmp_path):
  spec = importlib.util.spec_from_file_location("prepareEnterpriseScript", projectPath / "scripts/prepare_enterprise.py")
  script = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(script)
  prepareEnterprise = script.prepareEnterprise

  dbPath = tmp_path / "enterprise" / "knowledge.db"
  report = prepareEnterprise(dbPath=dbPath, importData=False)
  assert report["documentCount"] == 15
  assert report["revisionCount"] == 16
  assert not dbPath.exists()
  prepareEnterprise(dbPath=dbPath, importData=True)
  store = KnowledgeStore(dbPath)
  assert {item.documentId for item in store.listChunks(asOf="2026-10-22")} == {
    "demo-leave", "demo-attendance", "demo-manager", "demo-hr",
    "demo-emergency-leave", "demo-leave-change", "demo-handover", "demo-device-issue",
    "demo-offsite", "demo-attendance-discrepancy", "demo-manager-overlap", "demo-manager-return",
    "demo-hr-device", "demo-hr-announcement"}
  assert all(item.isDemo and "虛構" in item.text for item in store.listChunks(includeInactive=True))
  assert all(not item.externalProcessingAllowed for item in store.listChunks(includeInactive=True))
  store.setPublicationStatus("demo-leave", "v2", "withdrawn")
  prepareEnterprise(dbPath=dbPath, importData=True)
  assert "demo-leave" not in {item.documentId for item in store.listChunks(asOf="2026-11-01")}
  with pytest.raises(ValueError):
    prepareEnterprise(dbPath=projectPath / "data/index/knowledge.db", importData=True)
  legacyPath = tmp_path / "existing-public.db"
  KnowledgeStore(legacyPath).upsert([makeChunk()])
  originalBytes = legacyPath.read_bytes()
  with pytest.raises(ValueError):
    prepareEnterprise(dbPath=legacyPath, importData=True)
  assert legacyPath.read_bytes() == originalBytes
  result = subprocess.run([sys.executable, str(projectPath / "scripts/prepare_enterprise.py")],
    capture_output=True, text=True, encoding="utf-8")
  assert result.returncode == 0
  assert json.loads(result.stdout)["imported"] is False


def testFailedMigrationKeepsOriginalPayloadAndRollsBackLifecycleRows(tmp_path):
  path = tmp_path / "invalid-legacy.db"
  store = KnowledgeStore(path)
  store.upsert([makeChunk()])
  with sqlite3.connect(path) as connection:
    connection.execute("DELETE FROM revisionStates")
    connection.execute("INSERT INTO revisions VALUES (?, ?, ?)", ("invalid", "v1", "[]"))
    originalRows = connection.execute("SELECT * FROM revisions").fetchall()
  with pytest.raises(ValueError):
    KnowledgeStore(path)
  with sqlite3.connect(path) as connection:
    assert connection.execute("SELECT * FROM revisions").fetchall() == originalRows
    assert connection.execute("SELECT COUNT(*) FROM revisionStates").fetchone()[0] == 0
