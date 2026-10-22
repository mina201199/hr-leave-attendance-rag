import hashlib
import json
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory

from .auth import AuthStore
from .enterprise import EnterpriseService
from .ingestion import ENTERPRISE_SOURCE_TYPES, loadChunks
from .storage import KnowledgeStore


def runEvaluation(casesPath, corpusPath):
  casesPath, corpusPath = Path(casesPath), Path(corpusPath)
  corpus = loadChunks(corpusPath)
  if any(not chunk.isDemo or chunk.sourceType not in ENTERPRISE_SOURCE_TYPES
      or "虛構" not in chunk.text or "虛構" not in chunk.title or chunk.externalProcessingAllowed for chunk in corpus):
    raise ValueError("離線企業評估只接受明確標示虛構且禁止外送的示範語料")
  cases = [json.loads(line) for line in casesPath.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
  results = []
  with TemporaryDirectory(prefix="enterprise-evaluation-") as temporaryDir:
    directory = Path(temporaryDir)
    auth = AuthStore(directory / "auth.db")
    tokens = {}
    for role in ("employee", "manager", "hr"):
      password = secrets.token_urlsafe(24)
      auth.createUser(role, password, role)
      tokens[role] = auth.login(role, password)
    store = KnowledgeStore(directory / "knowledge.db")
    revisions = {}
    for chunk in corpus:
      revisions.setdefault((chunk.documentId, chunk.version), []).append(chunk)
    for revision in revisions.values():
      store.upsert(revision)
    service = EnterpriseService(auth, store)
    for case in cases:
      token = tokens[case["role"]]
      response = service.ask(token, case["question"], case["asOf"])
      sources = [hit.chunk for hit in response.hits] + response.citations
      documentIds = {chunk.documentId for chunk in sources}
      checks = {"source_scope": not bool(documentIds & set(case.get("forbiddenDocuments", []))),
        "response_valid": service.validateResponse(token, response, case["asOf"])}
      if "expectedDocument" in case:
        checks["expected_document"] = case["expectedDocument"] in documentIds
      if "expectedVersion" in case:
        versions = {chunk.version for chunk in sources if chunk.documentId == case["expectedDocument"]}
        checks["expected_version"] = versions == {case["expectedVersion"]}
      if "expectedStatus" in case:
        checks["expected_status"] = response.status == case["expectedStatus"]
      results.append({"id": case["id"], "passed": all(checks.values()), "checks": checks,
        "status": response.status, "documents": sorted(documentIds), "modelJudgment": "unassessed_offline"})
  passed = sum(result["passed"] for result in results)
  return {"caseCount": len(results), "passed": passed, "failed": len(results) - passed, "cases": results,
    "mode": "offline_source_excerpts", "networkCalls": 0, "modelJudgment": "unassessed_offline",
    "limitations": "案例與程式由同一實作流程撰寫，不是獨立人類保留集；只檢查離線來源、授權與版本，不評定真實模型的語意正確性、條件判斷或實際使用者驗收。",
    "inputHashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (casesPath, corpusPath)}}
