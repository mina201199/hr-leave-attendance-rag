import secrets
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput

from labor_rag.auth import AuthenticationError, AuthorizationError, AuthStore
from labor_rag.enterprise import EnterpriseService
from labor_rag.ingestion import loadChunks
from labor_rag.storage import KnowledgeStore


projectPath = Path(__file__).resolve().parents[1]


@pytest.fixture
def appSystem(tmp_path, monkeypatch):
  monkeypatch.setenv("RAG_ENTERPRISE_DATA_DIR", str(tmp_path))
  monkeypatch.setenv("RAG_PROVIDER", "extractive")
  monkeypatch.setenv("RAG_RETRIEVAL", "bm25")
  monkeypatch.setenv("OPENAI_API_KEY", "")
  auth = AuthStore(tmp_path / "auth.db")
  passwords = {}
  for userId, role, capabilities in (("employee", "employee", ()), ("other", "employee", ()),
      ("manager", "manager", ()), ("hr", "hr", ("knowledge:manage",)), ("reader", "hr", ())):
    passwords[userId] = secrets.token_urlsafe(24)
    auth.createUser(userId, passwords[userId], role, capabilities)
  store = KnowledgeStore(tmp_path / "knowledge.db")
  for chunk in loadChunks(projectPath / "data/company_demo/knowledge.jsonl"):
    store.upsert([chunk])
  service = EnterpriseService(auth, store)
  app = AppTest.from_file(str(projectPath / "enterprise_app.py"), default_timeout=20)
  return app, service, passwords


def login(app, passwords, userId):
  app.run()
  app.text_input(key="entLoginUser").set_value(userId)
  app.text_input(key="entLoginPassword").set_value(passwords[userId])
  app.button(key="entLoginSubmit").click().run()
  assert not app.exception
  assert "entLoginPassword" not in app.session_state
  assert "entLoginUser" not in app.session_state
  return app.session_state["entToken"]


def query(app, question="請假申請流程", category="請假", asOf=date(2026, 10, 22)):
  app.text_input(key="entQuestion").set_value(question)
  app.selectbox(key="entCategory").set_value(category)
  app.date_input(key="entAsOf").set_value(asOf)
  app.button(key="entAskSubmit").click().run()
  assert not app.exception
  return app.session_state["entResponse"]


def visibleText(app):
  return "\n".join(str(element.value) for kind in ("text", "caption", "info", "warning", "error", "success")
    for element in app.get(kind))


def test_login_boundary_and_empty_setup(appSystem):
  app, _, _ = appSystem
  app.run()
  assert not app.exception
  assert "虛構公司制度，僅供展示與測試" in visibleText(app)
  assert "scripts/prepare_enterprise.py" in visibleText(app)
  assert "scripts/manage_accounts.py" in visibleText(app)
  assert not app.tabs
  assert "demo-leave" not in visibleText(app)
  assert app.text_input(key="entLoginPassword").proto.type == TextInput.PASSWORD
  app.text_input(key="entLoginUser").set_value("unknown")
  app.text_input(key="entLoginPassword").set_value(secrets.token_urlsafe(24))
  app.button(key="entLoginSubmit").click().run()
  assert app.error
  assert not app.tabs
  assert app.text_input(key="entLoginPassword").value == ""
  assert app.session_state["entLoginPassword"] == ""
  assert app.text_input(key="entLoginUser").value == ""
  assert app.session_state["entLoginUser"] == ""
  assert "entToken" not in app.session_state
  # 清除後仍能用正確個人帳號重試，不能讓錯誤提示卡住登入。
  _, _, passwords = appSystem
  login(app, passwords, "employee")
  assert app.tabs


@pytest.mark.parametrize("userId,expected,managed", [
  ("employee", {"demo-leave", "demo-attendance"}, False),
  ("manager", {"demo-leave", "demo-attendance", "demo-manager"}, False),
  ("hr", {"demo-leave", "demo-attendance", "demo-manager", "demo-hr"}, True),
  ("reader", {"demo-leave", "demo-attendance", "demo-manager", "demo-hr"}, False),
])
def test_role_sources_query_and_backend_management(appSystem, userId, expected, managed):
  app, service, passwords = appSystem
  token = login(app, passwords, userId)
  app.date_input(key="entAsOf").set_value(date(2026, 10, 22)).run()
  text = visibleText(app)
  for documentId in ("demo-leave", "demo-attendance", "demo-manager", "demo-hr"):
    assert (documentId in text) == (documentId in expected)
  assert ("維護管理" in [tab.label for tab in app.tabs]) == managed
  if not managed:
    with pytest.raises(AuthorizationError):
      service.listManagedDocuments(token)
  response = query(app, "請假主管人資制度流程")
  assert response.status == "answered"
  assert {chunk.documentId for chunk in response.citations} <= expected
  assert "來源摘錄" in visibleText(app)
  assert "非模型回答" in visibleText(app)
  assert service.validateResponse(token, response)
  assert "internal://" not in "\n".join(element.value for element in app.markdown)


def test_category_and_effective_date_reach_service(appSystem):
  app, _, passwords = appSystem
  login(app, passwords, "employee")
  attendance = query(app, "補打卡說明流程", "出勤")
  assert {chunk.documentId for chunk in attendance.citations} == {"demo-attendance"}
  future = query(app, asOf=date(2026, 11, 1))
  assert {chunk.version for chunk in future.citations} == {"v2"}
  assert "交接代理安排" in visibleText(app)


def test_logout_clears_identity_widgets_results_and_owned_records(appSystem):
  app, service, passwords = appSystem
  oldToken = login(app, passwords, "employee")
  response = query(app)
  app.button(key="entHelpful").click().run()
  assert service.recentTraces(oldToken)[0]["feedback"] == "helpful"
  assert response.traceId in visibleText(app)
  app.button(key="entLogout").click().run()
  assert not app.tabs
  assert "entResponse" not in app.session_state
  assert "entQuestion" not in app.session_state
  assert "entAsOf" not in app.session_state
  assert response.traceId not in visibleText(app)
  with pytest.raises(AuthenticationError):
    service.authStore.authenticate(oldToken)
  newToken = login(app, passwords, "other")
  assert service.recentTraces(newToken) == []
  assert app.text_input(key="entQuestion").value == ""
  assert response.traceId not in visibleText(app)
  with pytest.raises(AuthorizationError):
    service.feedback(newToken, response.traceId, "helpful")


@pytest.mark.parametrize("change", ["disable", "downgrade"])
def test_revocation_clears_manager_ui_and_new_employee_cannot_reuse_evidence(appSystem, change):
  app, service, passwords = appSystem
  oldToken = login(app, passwords, "manager")
  response = query(app, "主管確認指引")
  assert "demo-manager" in {chunk.documentId for chunk in response.citations}
  assert response.traceId in visibleText(app)
  if change == "disable":
    service.authStore.setEnabled("manager", False)
  else:
    service.authStore.setRole("manager", "employee")
  app.run()
  assert not app.tabs
  assert "entToken" not in app.session_state
  assert "entResponse" not in app.session_state
  assert "entQuestion" not in app.session_state
  assert "demo-manager" not in visibleText(app)
  assert response.traceId not in visibleText(app)
  if change == "disable":
    return
  newToken = login(app, passwords, "manager")
  assert newToken != oldToken
  assert "demo-manager" not in visibleText(app)
  assert "demo-hr" not in visibleText(app)
  assert response.traceId not in visibleText(app)
  assert service.recentTraces(newToken) == []
  with pytest.raises(AuthorizationError):
    service.validateResponse(newToken, response)
  with pytest.raises(AuthorizationError):
    service.feedback(newToken, response.traceId, "helpful")
  app.session_state["entResponse"] = response
  app.run()
  assert not app.exception
  assert "entResponse" not in app.session_state
  assert "demo-manager" not in visibleText(app)


def test_withdrawn_answer_is_validated_before_render(appSystem):
  app, service, passwords = appSystem
  login(app, passwords, "employee")
  response = query(app)
  service.store.setPublicationStatus("demo-leave", "v1", "withdrawn")
  app.run()
  assert not app.exception
  assert "entResponse" not in app.session_state
  assert response.traceId not in visibleText(app)
  assert "demo-leave｜版本：v1" not in visibleText(app)


def test_management_controls_publish_withdraw_and_explicit_index_error(appSystem):
  app, service, passwords = appSystem
  token = login(app, passwords, "hr")
  assert not app.exception
  assert app.get("file_uploader")
  assert app.button(key="entPreview").disabled
  assert app.button(key="entImport").disabled
  app.selectbox(key="entRevision").set_value(("demo-draft", "v1")).run()
  app.button(key="entPublish").click().run()
  assert "demo-draft" in {chunk.documentId for chunk in service.listSources(token, "2026-10-22")}
  app.button(key="entWithdraw").click().run()
  assert "demo-draft" not in {chunk.documentId for chunk in service.listSources(token, "2026-10-22")}
  app.date_input(key="entIndexDate").set_value(date(2026, 11, 1))
  app.button(key="entBuildIndex").click().run()
  assert not app.exception
  assert "embedding provider" in visibleText(app)
  assert "API 費用" in visibleText(app)


def test_configuration_error_is_controlled(appSystem, monkeypatch):
  app, _, _ = appSystem
  monkeypatch.setenv("RAG_PROVIDER", "unknown")
  app.run()
  assert not app.exception
  assert app.error
  assert not app.tabs


def test_index_uses_selected_effective_date_without_automatic_work(appSystem, monkeypatch):
  app, service, passwords = appSystem

  class LocalEmbedder:
    externalProcessing = False
    modelId = "test-local"

    def embed(self, texts):
      return [[1.0, 0.0] for text in texts]

  service.embedder = LocalEmbedder()
  monkeypatch.setattr("labor_rag.enterprise.buildEnterpriseRuntime", lambda *args: service)
  token = login(app, passwords, "hr")
  futureChunks = service.listSources(token, "2026-11-01")
  assert service.store.loadVectors("test-local", futureChunks) == {}
  app.date_input(key="entIndexDate").set_value(date(2026, 11, 1))
  app.button(key="entBuildIndex").click().run()
  assert not app.exception
  assert "demo-leave-v2" in service.store.loadVectors("test-local", futureChunks)
  historicalChunks = service.listSources(token, "2026-10-22")
  assert "demo-leave" not in service.store.loadVectors("test-local", historicalChunks)
