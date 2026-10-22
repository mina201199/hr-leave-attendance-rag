
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st


projectPath = Path(__file__).resolve().parent
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.auth import AuthenticationError, AuthorizationError
from labor_rag.enterprise import buildEnterpriseRuntime
from labor_rag.settings import loadSettings


def clearIdentity():
  # 元件也可能保留上一位使用者的輸入或上傳，因此一併移除。
  for key in list(st.session_state):
    if key.startswith("ent"):
      del st.session_state[key]


def logout(service, token):
  try:
    service.authStore.logout(token)
  finally:
    clearIdentity()


def attemptLogin(service):
  try:
    token = service.authStore.login(st.session_state.get("entLoginUser", ""), st.session_state.get("entLoginPassword", ""))
  except AuthenticationError:
    clearIdentity()
    # callback 先於元件建立執行，可明確清除伺服器與畫面上的輸入。
    st.session_state["entLoginUser"] = ""
    st.session_state["entLoginPassword"] = ""
    st.session_state["entLoginError"] = True
  else:
    clearIdentity()
    st.session_state["entToken"] = token


def showLogin(service):
  st.info("尚未準備環境時，請本機管理者先執行 scripts/prepare_enterprise.py --import，"
    "再使用 scripts/manage_accounts.py 建立個人帳號。此頁沒有預設帳密或自動建立帳號。")
  st.caption("離線展示請設定 RAG_PROVIDER=extractive、RAG_RETRIEVAL=bm25。帳號維護僅由本機管理者執行。")
  if st.session_state.pop("entLoginError", False):
    st.error("登入失敗，請確認帳號、密碼與帳號狀態，或稍後再試。")
  with st.form("entLoginForm", clear_on_submit=True):
    st.text_input("個人帳號", key="entLoginUser", max_chars=64)
    st.text_input("密碼", type="password", key="entLoginPassword", max_chars=256)
    st.form_submit_button("登入", key="entLoginSubmit", on_click=attemptLogin, args=(service,))


def showChunk(chunk):
  st.text(chunk.title)
  if chunk.isDemo:
    st.caption("虛構示範來源，非真實公司文件")
  st.caption(f"{chunk.documentId}｜版本：{chunk.version}｜片段：{chunk.chunkId}")
  st.caption(f"類別：{ {'休假': '請假', '工時': '出勤'}.get(chunk.category, chunk.category)}｜"
    f"生效：{chunk.effectiveFrom or '未記錄'} 至 {chunk.effectiveTo or '未設定'}｜發布狀態：{chunk.publicationStatus}")
  st.text(chunk.text)


def showResponse(service, token):
  response = st.session_state.get("entResponse")
  if response is None:
    return
  try:
    service.validateResponse(token, response)
  except AuthorizationError:
    st.session_state.pop("entResponse", None)
    st.session_state.pop("entResponseDate", None)
    st.warning("先前答案的權限或來源版本已變更，請重新查詢。")
    return
  st.subheader("制度回答／來源摘錄")
  statusLabels = {"answered": "已找到來源", "no_evidence": "沒有足夠來源", "needs_clarification": "需要補充條件",
    "conflict": "來源有衝突", "context_limit": "來源超過內容預算"}
  st.caption(f"狀態：{statusLabels.get(response.status, response.status)}｜{response.mode}")
  st.text(response.text)
  st.caption("引用已通過權限與版本檢查；仍請核對原文中的適用條件。離線來源摘錄並非模型回答。")
  for citation in response.citations:
    # 內部識別碼只能經服務讀取，不產生可繞過授權的外部連結。
    authorized = service.readCitation(token, citation.chunkId, st.session_state.get("entResponseDate"), citation.version)
    with st.expander(f"引用：{authorized.title}（{authorized.version}）"):
      showChunk(authorized)
  st.caption(f"紀錄 ID：{response.traceId}｜耗時：{response.elapsedMs:.0f} ms")
  for label, key, rating in (("有幫助", "entHelpful", "helpful"), ("需要改善", "entUnhelpful", "unhelpful")):
    if st.button(label, key=key):
      service.feedback(token, response.traceId, rating)
      st.success("已儲存本人回饋。")


def showManagement(service, token, today):
  st.caption("上傳 UTF-8 JSONL，最多 1 MB。先預覽，再匯入草稿；發布與撤回須另外操作。")
  upload = st.file_uploader("制度 JSONL", type=["jsonl"], key="entUpload")
  jsonlText, uploadHash = None, None
  if upload is not None:
    data = upload.getvalue()
    if len(data) > 1_048_576:
      st.error("檔案超過 1 MB，請縮小後重新上傳。")
    else:
      try:
        jsonlText = data.decode("utf-8-sig")
        uploadHash = hashlib.sha256(data).hexdigest()
      except UnicodeError:
        st.error("請使用 UTF-8 JSONL。")
  if st.button("預覽匯入", key="entPreview", disabled=jsonlText is None):
    st.session_state.pop("entPreviewHash", None)
    service.previewImport(token, jsonlText)
    st.session_state["entPreviewHash"] = uploadHash
  previewReady = uploadHash is not None and uploadHash == st.session_state.get("entPreviewHash")
  if previewReady:
    st.subheader("草稿預覽（尚未儲存）")
    for chunk in service.previewImport(token, jsonlText):
      showChunk(chunk)
      st.caption(f"可讀角色：{', '.join(chunk.allowedRoles)}｜維護者：{chunk.maintainerId}｜"
        f"允許外部處理：{'是' if chunk.externalProcessingAllowed else '否'}")
  if st.button("確認匯入草稿", key="entImport", disabled=not previewReady):
    result = service.importDocuments(token, jsonlText)
    st.session_state.pop("entPreviewHash", None)
    st.session_state["entNotice"] = f"已匯入 {result['documentCount']} 份文件、{result['chunkCount']} 個片段；請另外確認發布。"
    st.rerun()
  documents = service.listManagedDocuments(token)
  revisions = sorted({(chunk.documentId, chunk.version) for chunk in documents})
  st.subheader("文件發布管理")
  if revisions:
    selected = st.selectbox("文件／版本", revisions, format_func=lambda item: f"{item[0]} / {item[1]}", key="entRevision")
    for chunk in documents:
      if (chunk.documentId, chunk.version) == selected:
        showChunk(chunk)
    for label, key, status in (("發布所選版本", "entPublish", "published"), ("撤回所選版本", "entWithdraw", "withdrawn")):
      if st.button(label, key=key):
        service.setPublicationStatus(token, selected[0], selected[1], status)
        st.session_state.pop("entResponse", None)
        st.session_state.pop("entResponseDate", None)
        st.session_state["entNotice"] = "已更新文件發布狀態。"
        st.rerun()
  else:
    st.info("目前沒有可維護的文件。")
  st.subheader("向量索引")
  st.warning("建立索引可能將允許外送的制度文字傳送至 OpenAI，並產生 API 費用；"
    "僅處理所選日期有效且您可讀的已發布版本。預設虛構制度禁止外送。未獲准付費測試前請勿執行雲端索引。")
  indexDate = st.date_input("索引適用日期", value=today, key="entIndexDate")
  if st.button("明確建立／更新向量索引", key="entBuildIndex"):
    result = service.indexMissing(token, asOf=indexDate.isoformat())
    st.success(f"新增 {result['indexed']} 個向量，沿用 {result['cached']} 個；API 回報 {result['totalTokens']} tokens。")


def showWorkspace(service, token, principal):
  today = datetime.now(ZoneInfo("Asia/Taipei")).date()
  roleLabels = {"employee": "員工", "manager": "主管", "hr": "人資"}
  st.caption(f"目前身分：{roleLabels[principal.role]}｜僅查制度，沒有個人假單、出勤與核准交易。")
  st.button("登出", key="entLogout", on_click=logout, args=(service, token))
  if "entNotice" in st.session_state:
    st.success(st.session_state.pop("entNotice"))
  canManage = principal.role == "hr" and "knowledge:manage" in principal.capabilities
  tabs = st.tabs(["制度查詢", "資料來源", "我的紀錄"] + (["維護管理"] if canManage else []))
  with tabs[0]:
    examples = ["請假申請流程是什麼？", "補打卡需要補充哪些說明？"]
    if principal.role in ("manager", "hr"):
      examples.append("主管收到申請後如何確認與退回？")
    if principal.role == "hr":
      examples.append("人資如何處理制度疑義與草稿？")
    st.text("可以試問：" + "；".join(examples))
    with st.form("entAskForm"):
      question = st.text_input("制度問題", key="entQuestion", max_chars=1000)
      category = st.selectbox("主題", ["全部", "請假", "出勤"], key="entCategory")
      asOf = st.date_input("制度適用日期", value=today, key="entAsOf")
      submitted = st.form_submit_button("查詢制度", key="entAskSubmit")
    if submitted:
      st.session_state.pop("entResponse", None)
      st.session_state.pop("entResponseDate", None)
      categoryValue = {"全部": None, "請假": "休假", "出勤": "工時"}[category]
      response = service.ask(token, question, asOf=asOf.isoformat(), category=categoryValue)
      st.session_state["entResponse"] = response
      st.session_state["entResponseDate"] = asOf.isoformat()
    showResponse(service, token)
  with tabs[1]:
    st.caption(f"以下為 {asOf.isoformat()} 當時有效、已發布且目前身分可讀的來源。內部來源以受控文字呈現。")
    for chunk in service.listSources(token, asOf=asOf.isoformat()):
      authorized = service.readCitation(token, chunk.chunkId, asOf.isoformat(), chunk.version)
      with st.expander(authorized.title):
        showChunk(authorized)
  with tabs[2]:
    st.caption("只顯示本人仍可讀的紀錄；不保留問題與回答原文。人資也只能查看自己的紀錄。")
    traces = service.recentTraces(token)
    if not traces:
      st.info("尚無可顯示的本人紀錄。")
    for trace in traces:
      st.text(f"紀錄 ID：{trace['traceId']}｜日期：{trace['asOf']}｜建立時間：{trace['createdAt']}")
      st.caption(f"模式：{trace['mode']}｜狀態：{trace['status']}｜回饋：{trace['feedback'] or '尚未提供'}")
      st.text("來源版本：" + ", ".join(f"{ref['chunkId']} / {ref['version']}" for ref in trace["sourceVersions"]))
  if canManage:
    with tabs[3]:
      showManagement(service, token, today)


st.set_page_config(page_title="企業請假與出勤制度助理", page_icon="📚", layout="wide")
st.title("企業請假與出勤制度助理")
st.warning("虛構公司制度，僅供展示與測試")
st.caption("本機展示介面；公司內規是否可用仍待確認。")

try:
  settings = loadSettings(projectPath)
  service = buildEnterpriseRuntime(projectPath, settings)
except (ValueError, OSError, sqlite3.Error, ImportError) as error:
  clearIdentity()
  st.error(f"企業環境設定無法載入：{error}")
  st.stop()

token = st.session_state.get("entToken")
if token is None:
  showLogin(service)
else:
  try:
    # 每次重繪重新驗證；Principal 僅存於本次執行，不放入快取或 session。
    principal = service.authStore.authenticate(token)
    showWorkspace(service, token, principal)
  except AuthenticationError:
    clearIdentity()
    st.rerun()
  except AuthorizationError:
    st.session_state.pop("entResponse", None)
    st.session_state.pop("entResponseDate", None)
    st.error("目前沒有權限執行此操作，請重新查詢或聯絡本機管理者。")
  except (ValueError, OSError, sqlite3.Error) as error:
    st.error(f"操作無法完成：{error}")
