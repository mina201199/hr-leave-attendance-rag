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
  app.selectbox(key="entCategory").set_value(category)
  app.date_input(key="entAsOf").set_value(asOf)
  app.chat_input(key="entQuestion").set_value(question).run()
  assert not app.exception
  return app.session_state["entConversation"][-1]["response"]


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
  app.button(key=f"entHelpful-{response.traceId}").click().run()
  assert service.recentTraces(oldToken)[0]["feedback"] == "helpful"
  assert response.traceId in visibleText(app)
  app.button(key="entLogout").click().run()
  assert not app.tabs
  assert ("entConversation" not in app.session_state or not app.session_state["entConversation"])
  assert "entQuestion" not in app.session_state
  assert "entAsOf" not in app.session_state
  assert response.traceId not in visibleText(app)
  with pytest.raises(AuthenticationError):
    service.authStore.authenticate(oldToken)
  newToken = login(app, passwords, "other")
  assert service.recentTraces(newToken) == []
  assert not app.chat_input(key="entQuestion").value
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
  assert ("entConversation" not in app.session_state or not app.session_state["entConversation"])
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
  app.session_state["entConversation"] = [{"question": "主管確認指引", "response": response, "asOf": "2026-10-22", "category": "請假"}]
  app.run()
  assert not app.exception
  assert ("entConversation" not in app.session_state or not app.session_state["entConversation"])
  assert "demo-manager" not in visibleText(app)


def test_withdrawn_answer_is_validated_before_render(appSystem):
  app, service, passwords = appSystem
  login(app, passwords, "employee")
  response = query(app)
  service.store.setPublicationStatus("demo-leave", "v1", "withdrawn")
  app.run()
  assert not app.exception
  assert ("entConversation" not in app.session_state or not app.session_state["entConversation"])
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
  assert app.button(key="entWithdraw").disabled
  assert "草稿尚未發布，無法撤回。" in visibleText(app)
  app.button(key="entPublish").click().run()
  assert not app.exception
  assert not app.button(key="entWithdraw").disabled
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


def testConversationKeepsTurnsAndClearsWithoutDeletingOwnedRecords(appSystem):
  app, service, passwords = appSystem
  token = login(app, passwords, "employee")
  first = query(app, "請假申請流程")
  second = query(app, "補打卡說明流程", "出勤")
  assert [message.name for message in app.chat_message] == ["user", "assistant", "user", "assistant"]
  assert "請假申請流程" in visibleText(app)
  assert first.traceId in visibleText(app)
  assert second.traceId in visibleText(app)
  app.button(key=f"entHelpful-{first.traceId}").click().run()
  assert next(trace for trace in service.recentTraces(token) if trace["traceId"] == first.traceId)["feedback"] == "helpful"
  app.button(key="entClearConversation").click().run()
  assert [message.name for message in app.chat_message] == ["assistant"]
  assert "你好，請告訴我" in visibleText(app)
  assert app.session_state["entConversation"] == []
  assert service.authStore.authenticate(token).role == "employee"
  assert len(service.recentTraces(token)) == 2


def testOlderWithdrawnTurnRemovesQuestionAndAnswerButKeepsAuthorizedTurn(appSystem):
  app, service, passwords = appSystem
  login(app, passwords, "employee")
  first = query(app, "請假申請流程與專屬識別碼QA")
  second = query(app, "補打卡說明流程", "出勤")
  service.store.setPublicationStatus("demo-leave", "v1", "withdrawn")
  app.run()
  assert not app.exception
  assert len(app.chat_message) == 2
  assert "專屬識別碼QA" not in visibleText(app)
  assert first.traceId not in visibleText(app)
  assert second.traceId in visibleText(app)
  assert len(app.session_state["entConversation"]) == 1


def testConversationRetainsEachTurnDateAndBoundsMemory(appSystem):
  app, _, passwords = appSystem
  login(app, passwords, "employee")
  first = query(app, asOf=date(2026, 10, 31))
  second = query(app, asOf=date(2026, 11, 1))
  assert {chunk.version for chunk in first.citations} == {"v1"}
  assert {chunk.version for chunk in second.citations} == {"v2"}
  assert [turn["asOf"] for turn in app.session_state["entConversation"]] == ["2026-10-31", "2026-11-01"]
  assert "適用日期：2026-10-31" in visibleText(app)
  assert "適用日期：2026-11-01" in visibleText(app)
  for index in range(9):
    query(app, f"補打卡說明流程 {index}", "出勤")
  assert len(app.session_state["entConversation"]) == 10
  assert all(turn["response"].traceId != first.traceId for turn in app.session_state["entConversation"])


@pytest.mark.parametrize("asOf,dateNote,condition", [
  (date(2026, 10, 22), "本示範流程適用於 2026-11-01 前的制度查詢。", "需要調整時由主管退回說明"),
  (date(2026, 11, 1), "本示範流程自 2026-11-01 起適用。", "代理安排尚未確認時先補齊說明再送出"),
])
def testDemoNotesAppearBelowAnswerAndOriginalCitationStaysIntact(appSystem, asOf, dateNote, condition):
  app, _, passwords = appSystem
  login(app, passwords, "employee")
  response = query(app, asOf=asOf)
  assistant = app.chat_message[-1]
  body = assistant.get("text")[0].value
  assert body.startswith("員工先填寫")
  assert condition in body
  assert "虛構" not in body
  assert dateNote not in body
  assert "來源識別碼" not in body
  captions = "\n".join(element.value for element in assistant.get("caption"))
  assert dateNote in captions
  assert "虛構示範制度，非真實公司文件" in captions
  assert response.citations[0].text in [element.value for element in assistant.get("text")]
