import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

import streamlit as st


projectPath = Path(__file__).resolve().parent
sys.path.insert(0, str(projectPath / "src"))

from labor_rag.evaluation import evaluateCases
from labor_rag.ingestion import PENALTY_SOURCE_URL, loadChunks, parsePenaltyCsv
from labor_rag.retrieval import Retriever
from labor_rag.hybrid import LocalCrossEncoder
from labor_rag.processing import splitChunk
from labor_rag.runtime import buildRuntime, indexMissing
from labor_rag.settings import loadSettings


@st.cache_resource
def loadReranker(modelPath):
  return LocalCrossEncoder(modelPath)


def sourceLink(url):
  return quote(url, safe=":/?&=#%")


st.set_page_config(page_title="打工權益資料助理", page_icon="📚", layout="wide")
st.title("打工權益資料助理")
st.caption("薪資・工時・休假｜以官方資料查詢權益，附上可核對的來源")

try:
  faqChunks = loadChunks(projectPath / "data/seed/official_faq.jsonl")
  settings = loadSettings(projectPath)
  reranker = loadReranker(settings.rerankerPath) if settings.rerankerPath else None
  runtime = buildRuntime(projectPath, settings, reranker)
except (ValueError, OSError, sqlite3.Error, ImportError) as error:
  st.error(f"知識庫載入失敗：{error}")
  st.stop()

with st.sidebar:
  st.header("資料範圍")
  st.info(f"回答：{runtime.service.generator.mode}\n\n檢索：{runtime.retriever.mode}")
  st.caption("重排序：本機 CrossEncoder" if settings.rerankerPath else "重排序：RRF 排序；未啟用 CrossEncoder")
  if settings.provider == "openai" or settings.retrievalMode == "hybrid":
    st.caption("雲端模式會將問題及選中的來源文字傳送至 OpenAI。建立向量索引也會傳送目前公開來源文字，並產生 API 費用。")
  else:
    st.caption("目前不呼叫雲端模型。依 .env.example 設定金鑰與模型後可啟用 OpenAI。")
  st.caption("資料包含官方問答、整理摘要及歷史裁罰公告；請由來源連結核對適用條件與最新版本。")
  st.caption(f"有效資料：{len({item.documentId for item in runtime.chunks})} 筆文件／案件，{len(runtime.chunks)} 個檢索片段。")
  st.markdown(f"[下載臺北市勞動裁罰資料]({PENALTY_SOURCE_URL})")
  uploadedFile = st.file_uploader("匯入官方裁罰 CSV", type=["csv"], key="penaltyFile")
  st.caption("支援 UTF-8 CSV，最多 10 MB。按下匯入才會儲存於本機；系統不驗證上傳內容真偽，請僅匯入已核對的公開資料。")
  if st.button("確認匯入並儲存", disabled=uploadedFile is None, key="importPenalty"):
    try:
      imported = parsePenaltyCsv(uploadedFile.getvalue().decode("utf-8-sig"))
      runtime.store.upsert([part for chunk in imported for part in splitChunk(chunk)])
      st.session_state.pop("lastResponse", None)
      st.session_state["notice"] = f"已處理 {len(imported)} 筆裁罰資料；相同案件與內容不重複儲存。"
      st.rerun()
    except (ValueError, UnicodeError, sqlite3.Error) as error:
      st.error(f"裁罰檔案無法匯入：{error}")
  if runtime.embedder is not None:
    st.caption(f"待建立向量：{runtime.missingVectors} 個片段")
    if st.button("建立／更新 OpenAI 向量索引", key="buildIndex"):
      try:
        with st.spinner("正在建立向量索引…"):
          result = indexMissing(runtime.store, runtime.embedder)
        st.session_state["notice"] = f"新增 {result['indexed']} 個向量；API 回報 {result['totalTokens']} tokens。"
        st.rerun()
      except (ValueError, sqlite3.Error) as error:
        st.error(str(error))

if "notice" in st.session_state:
  st.success(st.session_state.pop("notice"))
chunks = runtime.chunks
questionTab, sourcesTab, evaluationTab, operationsTab = st.tabs(["權益與裁罰查詢", "資料來源", "檢索評估", "查詢紀錄"])

with questionTab:
  pendingIndex = settings.retrievalMode == "hybrid" and runtime.missingVectors > 0
  if pendingIndex:
    st.warning("混合檢索的向量索引尚未完整，請先在側欄建立／更新索引。")
  st.markdown("""可以試問：

- **薪資**：打破杯子，老闆能直接從薪水扣賠償費嗎？
- **薪資**：公司發薪水時一定要給薪資明細嗎？可以用電子郵件寄嗎？
- **工時**：下班回家後老闆又交辦工作，這段時間有加班費嗎？
- **工時**：加班跨過午夜，工時可以分成兩天計算嗎？
- **休假**：兼職每週上班天數跟正職一樣、每天時數較短，特休怎麼給？
- **休假**：老闆可以未經我同意把病假改成特休嗎？
- **休假**：工讀生休息日加班，可是當週還沒做滿四十小時，需要加給工資嗎？
- **裁罰**：請列出這家公司的裁罰處分字號、違規內容及公告中的罰鍰金額。
""")
  st.caption("試問裁罰時，主題選「裁罰」，公司名稱可填「剛剛股份有限公司」；結果為已收錄的歷史公告。")
  with st.form("askForm"):
    question = st.text_input("你的問題", key="question", max_chars=1000)
    firstColumn, secondColumn, thirdColumn = st.columns([1, 2, 1])
    category = firstColumn.selectbox("主題", ["全部", "薪資", "工時", "休假", "裁罰"])
    companyName = secondColumn.text_input("公司名稱（選填，完全相符）", help="公司條件只搜尋匯入的裁罰紀錄。")
    topK = thirdColumn.selectbox("顯示來源數", [3, 5, 1])
    submitted = st.form_submit_button("查詢資料", key="searchSubmit", type="primary", disabled=pendingIndex)
  if submitted:
    st.session_state.pop("lastResponse", None)
    try:
      categoryFilter = None if category == "全部" else category
      companyFilter = companyName.strip() or None
      with st.spinner("正在檢索與整理來源…"):
        response = runtime.service.ask(question, topK, categoryFilter, companyFilter)
      st.session_state["lastResponse"] = response
    except (ValueError, sqlite3.Error) as error:
      st.error(str(error))
  response = st.session_state.get("lastResponse")
  if response is not None:
    if response.status != "answered":
      st.warning(response.text)
    else:
      st.subheader("最相關資料摘錄" if response.mode == "來源摘錄" else "依來源整理的回答")
      st.text(response.text)
      st.caption("來源摘錄未使用 LLM。" if response.mode == "來源摘錄" else "每個重點附有引用 ID；系統檢查 ID 是否存在，內容支持度仍須核對原文。")
      for citation in response.citations:
        st.markdown(f"引用來源 {citation.chunkId}：[官方頁面]({sourceLink(citation.sourceUrl)})")
    if response.hits:
      st.subheader("檢索結果")
      for rank, hit in enumerate(response.hits, 1):
        with st.expander(f"{rank}. {hit.chunk.title}", expanded=rank == 1):
          st.text(hit.chunk.text)
          if hit.chunk.parentText:
            st.caption("所屬段落全文")
            st.text(hit.chunk.parentText)
          st.markdown(f"[開啟來源]({sourceLink(hit.chunk.sourceUrl)})")
          st.caption(f"{hit.chunk.publisher}｜{hit.chunk.sourceType}｜版本：{hit.chunk.version}｜整理日期：{hit.chunk.collectedAt}｜來源更新：{hit.chunk.sourceUpdatedAt or '未記錄'}")
          st.caption(f"片段 ID：{hit.chunk.chunkId}｜排序分數：{hit.score:.4f}（不是可信度）")
    st.caption(f"模式：{response.mode}｜耗時：{response.elapsedMs:.0f} ms｜紀錄 ID：{response.traceId}")
    helpfulColumn, unhelpfulColumn, _ = st.columns([1, 1, 4])
    for column, label, key, rating in [(helpfulColumn, "有幫助", "feedbackHelpful", "helpful"), (unhelpfulColumn, "需要改善", "feedbackUnhelpful", "unhelpful")]:
      if column.button(label, key=key):
        runtime.traces.feedback(response.traceId, rating)
        st.success("回饋已儲存。")
    st.caption("裁罰查詢僅限已匯入資料，查無紀錄不代表沒有違規。請連同處分日期與備註判讀。")

with sourcesTab:
  st.subheader("目前可檢索資料")
  st.write(f"目前可查詢 {len(chunks)} 個公開片段；儲存庫保留 {len(runtime.store.listChunks(includeInactive=True))} 個版本片段。")
  st.dataframe([
    {"標題": chunk.title, "主題": chunk.category, "來源類型": chunk.sourceType, "版本": chunk.version, "文件 ID": chunk.documentId, "來源網址": chunk.sourceUrl}
    for chunk in chunks
  ], hide_index=True, width="stretch")
  st.caption("範例資料供驗證流程使用，尚未涵蓋全部勞動法規。來源更新日與實際適用年度須分別核對。")

with evaluationTab:
  st.subheader("固定題組：檢索流程檢查")
  st.caption("這是 7 題開發用範例，僅使用隨附 FAQ，不代表正式專題測試成績。正式研究需另建獨立題組。")
  if st.button("執行範例評估", key="evaluate"):
    casesPath = projectPath / "data/evaluation/smoke.jsonl"
    cases = [json.loads(line) for line in casesPath.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = evaluateCases(Retriever(faqChunks), cases)
    leftColumn, rightColumn = st.columns(2)
    leftColumn.metric("平均 Recall@3", f"{report['meanRecallAtK']:.0%}")
    rightColumn.metric("查無結果正確率", f"{report['noEvidenceAccuracy']:.0%}")
    st.dataframe(report["details"], hide_index=True, width="stretch")
    st.caption("答案正確性與引用支持度仍須人工評分；本表只計算檢索結果。")
  st.caption("本頁固定測試 BM25 基線；比較目前混合檢索請使用 scripts/evaluate.py --mode configured，結果會標示設定。")

with operationsTab:
  st.subheader("本機查詢紀錄與回饋")
  st.caption("記錄耗時、片段 ID、來源版本、模型、token 用量與回饋；不儲存問題或答案原文。此介面為單機專題用途，尚未提供多人登入。")
  records = runtime.traces.recent()
  if records:
    st.dataframe([{"時間": row["createdAt"], "狀態": row["status"], "模式": row["mode"], "耗時 ms": row["elapsedMs"], "回饋": row["feedback"], "紀錄 ID": row["traceId"]} for row in records], hide_index=True, width="stretch")
    with st.expander("查看詳細紀錄"):
      st.json(records)
  else:
    st.info("送出第一個問題後，會在這裡顯示紀錄。")
