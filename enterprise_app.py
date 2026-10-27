
import hashlib
import re
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
from labor_rag.enterprise_display import conciseExcerpt
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


def withoutCitationMarkers(text, citations):
  # 只隱藏服務附加在段尾的有效引用代碼，保留正文中的括號與原始回答。
  markers = "|".join(re.escape(f"[{citation.chunkId}]") for citation in citations)
  return re.sub(rf"(?:[ \t]+(?:{markers}))+(?=\n|$)", "", text) if markers else text


def answerDisplayParts(response, question=""):
  if response.mode != "企業來源摘錄（離線，非模型回答）":
    return withoutCitationMarkers(response.text, response.citations), []
  body = response.text.removeprefix("【來源摘錄，非模型回答；虛構示範依各來源標示】\n\n")
  notes = []
  primaryExcerpt = None
  demoMarker = "【虛構示範制度，非真實公司文件】"
  demoFooters = (
    "本頁只說明虛構操作流程，不規定法定假別日數或薪資。",
    "本頁只改變虛構流程，不規定法定假別日數或薪資。",
    "本系統只查詢流程，不讀取個人出勤、不接受實際補卡申請。",
    "本文件只提供主管與人資的虛構作業指引，不授權查閱個人紀錄或執行真實核准交易。",
    "本流程不含員工個資、實際假單或法定權益判斷。",
  )
  for citation in response.citations:
    original = f"{citation.text} [{citation.chunkId}]"
    if not citation.isDemo or not citation.text.startswith(demoMarker) or original not in body:
      continue
    # 只搬移已知展示格式，不用泛用句子規則刪掉制度條件；引用原文保持完整。
    excerpt = citation.text.removeprefix(demoMarker)
    details = ["虛構示範制度，非真實公司文件。"]
    dateNote = re.match(r"本示範流程(?:適用於 \d{4}-\d{2}-\d{2} 前的制度查詢|自 \d{4}-\d{2}-\d{2} 起適用)。", excerpt)
    if dateNote:
      details.append(dateNote[0])
      excerpt = excerpt[len(dateNote[0]):]
    excerpt = excerpt.removesuffix(f"來源識別碼：{citation.documentId}。")
    for footer in demoFooters:
      if excerpt.endswith(footer):
        excerpt = excerpt.removesuffix(footer)
        details.append(footer)
        break
    if not excerpt.strip():
      continue
    if citation == response.citations[0]:
      primaryExcerpt = excerpt
    body = body.replace(original, f"{excerpt} [{citation.chunkId}]", 1)
    notes.append(f"[{citation.chunkId}] " + "".join(details))
  if response.status == "answered" and primaryExcerpt is not None:
    body = conciseExcerpt(primaryExcerpt, question)
  return withoutCitationMarkers(body, response.citations), notes


def showResponse(service, token, response, asOf, question):
  statusLabels = {"answered": "已找到來源", "no_evidence": "沒有足夠來源", "needs_clarification": "需要補充條件",
    "conflict": "來源有衝突", "context_limit": "來源超過內容預算"}
  body, notes = answerDisplayParts(response, question)
  st.text(body)
  if response.citations:
    with st.expander(f"查看引用（{len(response.citations)}）"):
      for citation in response.citations:
        # 收合只改變呈現，每份原文仍必須經過後端驗權。
        authorized = service.readCitation(token, citation.chunkId, asOf, citation.version)
        showChunk(authorized)
  with st.expander("來源與適用說明", expanded=False):
    for note in notes:
      st.caption(note)
    st.caption(f"狀態：{statusLabels.get(response.status, response.status)}｜{response.mode}")
    st.caption("離線重點摘錄：以本機文字比對整理最相關來源，並非模型回答；完整條件與其他檢索結果請展開引用確認。")
    st.caption(f"紀錄 ID：{response.traceId}｜耗時：{response.elapsedMs:.0f} ms")
  feedbackColumns = st.columns([1, 1, 3])
  for index, (label, key, rating) in enumerate((("有幫助", "entHelpful", "helpful"), ("需要改善", "entUnhelpful", "unhelpful"))):
    if feedbackColumns[index].button(label, key=f"{key}-{response.traceId}"):
      service.feedback(token, response.traceId, rating)
      st.success("已儲存本人回饋。")


def clearConversation():
  st.session_state["entConversation"] = []
  st.session_state.pop("entQuestion", None)
  st.session_state.pop("entPendingQuestion", None)
  st.session_state["entPage"] = "制度查詢"


def queueSuggestedQuestion(question):
  st.session_state["entPendingQuestion"] = question


def showConversation(service, token, asOf):
  history = st.session_state.get("entConversation", [])
  # 與原生輸入框共用底部區域，避免固定定位偏移或遮住側欄。
  suggestionsArea = st._bottom.container()
  typedQuestion = st.chat_input("輸入請假或出勤制度問題…", key="entQuestion", max_chars=1000)
  suggestedQuestion = st.session_state.pop("entPendingQuestion", None)
  question = typedQuestion or suggestedQuestion
  if question:
    response = service.ask(token, question, asOf=asOf.isoformat())
    history = (history + [{"question": question, "response": response,
      "asOf": asOf.isoformat()}])[-10:]
  st.caption("請假與出勤助理")
  if not history:
    st.title("今天想了解什麼？")
    st.caption("直接問我請假或出勤制度，也可以從常用問題開始。")
  commonQuestions = (
    ("demo-emergency-leave", "臨時請假要通知誰？"),
    ("demo-leave-change", "請假日期變更或取消要怎麼辦？"),
    ("demo-attendance", "補打卡要填寫哪些說明？"),
    ("demo-device-issue", "打卡設備故障時要怎麼通報？"),
    ("demo-manager-overlap", "主管遇到多人同時請假怎麼處理？"),
    ("demo-manager-return", "主管退回申請時要註明什麼？"),
    ("demo-hr-device", "人資如何處理打卡設備故障通報？"),
    ("demo-hr-announcement", "人資發布新版制度前要確認什麼？"),
  )
  readableDocuments = {chunk.documentId for chunk in service.listSources(token, asOf.isoformat())}
  questions = [(documentId, question) for documentId, question in commonQuestions if documentId in readableDocuments]
  with suggestionsArea:
    if questions:
      with st.expander("常用問題", expanded=not history):
        columns = st.columns(2)
        for index, (documentId, question) in enumerate(questions):
          columns[index % 2].button(question, key=f"entSuggested-{documentId}", use_container_width=True,
            on_click=queueSuggestedQuestion, args=(question,))
  conversationArea = st.container()
  authorizedHistory = []
  for turn in history:
    try:
      # 舊問題也可能帶有制度內容，先驗權再顯示整輪對話。
      service.validateResponse(token, turn["response"], asOf=turn["asOf"])
    except AuthorizationError:
      continue
    authorizedHistory.append(turn)
  st.session_state["entConversation"] = authorizedHistory
  with conversationArea:
    if len(authorizedHistory) != len(history):
      st.warning("部分對話的權限或來源版本已變更，已移除該輪問題與回答；請重新查詢。")
    if not authorizedHistory:
      with st.chat_message("assistant", avatar="✨"):
        st.text("你好，請告訴我想了解的請假或出勤制度。我會依你的權限提供來源，方便你核對。")
        st.caption("例如：臨時請假要通知誰？補打卡需要哪些說明？")
    for turn in authorizedHistory:
      with st.chat_message("user"):
        st.text(turn["question"])
        st.caption(f"適用日期：{turn['asOf']}")
      with st.chat_message("assistant", avatar="✨"):
        showResponse(service, token, turn["response"], turn["asOf"], turn["question"])


def showManagement(service, token, today):
  # 傳輸層須容納 multipart 額外資料；隱藏它的大小提示，改顯示實際的檔案限制。
  st.html("<style>[data-testid='stFileUploaderDropzoneInstructions'] { display: none; }</style>")
  st.caption("上傳 UTF-8 JSONL，最多 1 MiB（1,048,576 bytes）。先預覽，再匯入草稿；發布與撤回須另外操作。")
  upload = st.file_uploader("制度 JSONL", type=["jsonl"], key="entUpload")
  jsonlText, uploadHash = None, None
  if upload is not None:
    data = upload.getvalue()
    if len(data) > 1_048_576:
      st.error("檔案超過 1 MiB，請縮小後重新上傳。")
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
    selectedIsDraft = any(chunk.publicationStatus == "draft" for chunk in documents
      if (chunk.documentId, chunk.version) == selected)
    for chunk in documents:
      if (chunk.documentId, chunk.version) == selected:
        showChunk(chunk)
    if selectedIsDraft:
      st.caption("草稿尚未發布，無法撤回。")
    for label, key, status in (("發布所選版本", "entPublish", "published"), ("撤回所選版本", "entWithdraw", "withdrawn")):
      if st.button(label, key=key, disabled=status == "withdrawn" and selectedIsDraft):
        service.setPublicationStatus(token, selected[0], selected[1], status)
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
  canManage = principal.role == "hr" and "knowledge:manage" in principal.capabilities
  with st.sidebar:
    st.subheader("制度助理")
    st.button("＋ 新對話", key="entClearConversation", on_click=clearConversation, use_container_width=True)
    page = st.radio("功能選單", ["制度查詢", "資料來源", "我的紀錄"] + (["維護管理"] if canManage else []),
      key="entPage", label_visibility="collapsed")
    with st.expander("對話設定"):
      asOf = st.date_input("制度適用日期", value=today, key="entAsOf")
      st.caption("日期影響下一題與資料來源清單，舊對話保留原日期。每題請提供完整條件，目前不做跨輪推論。")
      st.caption("最近 10 輪僅保留在本次登入畫面；新對話會清空畫面，登出即清除。")
    st.divider()
    st.caption(f"目前身分：{roleLabels[principal.role]}")
    st.caption("虛構公司制度，僅供展示與測試")
    st.caption("僅查制度，不提供個人出勤或核准交易。")
    st.button("登出", key="entLogout", on_click=logout, args=(service, token))
  if "entNotice" in st.session_state:
    st.success(st.session_state.pop("entNotice"))
  if page == "制度查詢":
    showConversation(service, token, asOf)
  elif page == "資料來源":
    st.title("資料來源")
    st.caption(f"以下為 {asOf.isoformat()} 當時有效、已發布且目前身分可讀的來源。內部來源以受控文字呈現。")
    for chunk in service.listSources(token, asOf=asOf.isoformat()):
      authorized = service.readCitation(token, chunk.chunkId, asOf.isoformat(), chunk.version)
      with st.expander(authorized.title):
        showChunk(authorized)
  elif page == "我的紀錄":
    st.title("我的紀錄")
    st.caption("只顯示本人仍可讀的紀錄；不保留問題與回答原文。人資也只能查看自己的紀錄。")
    traces = service.recentTraces(token)
    if not traces:
      st.info("尚無可顯示的本人紀錄。")
    for trace in traces:
      st.text(f"紀錄 ID：{trace['traceId']}｜日期：{trace['asOf']}｜建立時間：{trace['createdAt']}")
      st.caption(f"模式：{trace['mode']}｜狀態：{trace['status']}｜回饋：{trace['feedback'] or '尚未提供'}")
      st.text("來源版本：" + ", ".join(f"{ref['chunkId']} / {ref['version']}" for ref in trace["sourceVersions"]))
  elif page == "維護管理" and canManage:
    st.title("維護管理")
    showManagement(service, token, today)


st.set_page_config(page_title="企業請假與出勤制度助理", page_icon="📚", layout="wide")
st.html("""<style>
  .stMainBlockContainer { max-width: 54rem; padding-top: 2rem; padding-bottom: 5rem; }
  [data-testid="stSidebar"] { background: #f1f3f5; }
  [data-testid="stChatMessage"] { background: transparent; padding: 1rem 0; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: #e9edf2; border-radius: 1.4rem; padding: 1rem 1.3rem;
    margin-left: auto; max-width: 85%; width: fit-content;
  }
  [data-testid="stChatMessageAvatarUser"] { display: none; }
  [data-testid="stChatInput"] { border-radius: 1.5rem; }
  [data-testid="stBottomBlockContainer"] { max-width: 54rem; margin: auto; }
  [data-testid="stText"] { line-height: 1.85; }
  [data-testid="stAppDeployButton"] { display: none; }
  @media (max-width: 640px) {
    .stMainBlockContainer { padding-left: 1rem; padding-right: 1rem; }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) { max-width: 95%; }
  }
</style>""")

try:
  settings = loadSettings(projectPath)
  service = buildEnterpriseRuntime(projectPath, settings)
except (ValueError, OSError, sqlite3.Error, ImportError) as error:
  clearIdentity()
  st.error(f"企業環境設定無法載入：{error}")
  st.stop()

token = st.session_state.get("entToken")
if token is None:
  st.title("企業制度助理")
  st.caption("登入後，開始你的請假與出勤制度對話。")
  st.warning("虛構公司制度，僅供展示與測試")
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
    if st.session_state.get("entConversation"):
      clearConversation()
      st.session_state["entNotice"] = "權限或來源已變更，已清除對話，請重新查詢。"
      st.rerun()
    st.error("目前沒有權限執行此操作，請重新查詢或聯絡本機管理者。")
  except (ValueError, OSError, sqlite3.Error) as error:
    st.error(f"操作無法完成：{error}")
