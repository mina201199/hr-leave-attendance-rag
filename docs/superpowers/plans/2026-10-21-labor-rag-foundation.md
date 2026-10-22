# Labor RAG Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可在本機操作的官方資料檢索、裁罰匯入與來源摘錄框架。

**Architecture:** Python 四模組，共用資料契約。Streamlit 串接中文 BM25 與可替換的回答生成介面，預設來源摘錄模式。

**Tech Stack:** Python >=3.11、Streamlit >=1.51,<2、pytest >=8,<9。

**Spec:** `docs/architecture.md`

## Global Constraints

- 縮排 2 個空白；變數與函式 camelCase；介面、註解與說明使用正體中文。
- 只新增檔案，不修改原有架構圖；尚未指定模型，不發送遠端模型請求。
- 公開來源可追溯；裁罰實際內容由使用者提供官方 CSV，不捏造正式紀錄。
- 無 Git repository，不執行提交或建立 worktree。

## Task 1: 四模組資料流

**Files:** `src/labor_rag/{__init__,models,ingestion,retrieval,generation,service}.py`、`tests/test_pipeline.py`

**Interfaces:** `loadChunks(path) -> list[DocumentChunk]`、`parsePenaltyCsv(text) -> list[DocumentChunk]`、`Retriever.search(question, topK=3, category=None, companyName=None) -> list[SearchHit]`、`RagService.ask(question, topK=3, category=None, companyName=None) -> AnswerResponse`。

- [x] 先建立 `tests/test_pipeline.py`，使用暫存 JSONL／CSV，驗證同義查詢、類別隔離、完全相符公司查詢、空輸入、無證據、無效引用與 UTF-8 BOM。
- [x] 執行 `python -m pytest tests/test_pipeline.py -q`，確認缺少模組導致尚未通過。
- [x] 建立共用 dataclass、驗證匯入、中文 BM25、來源摘錄生成器與協調器。
- [x] 再執行測試，所有情境通過；Windows 暫存目錄改為專案內獨立目錄。

## Task 2: 可操作介面與評估

**Files:** `app.py`、`data/seed/official_faq.jsonl`、`data/evaluation/smoke.jsonl`、`src/labor_rag/evaluation.py`、`tests/test_app.py`、`tests/test_evaluation.py`、`README.md`

**Interfaces:** `evaluateCases(retriever, cases, topK=3) -> dict`；Streamlit 呼叫 Task 1 的 `RagService.ask`。

- [x] 建立 AppTest，驗證「打破杯子扣薪」能顯示正確來源，資料不足有清楚訊息。
- [x] 建立評估測試，以一題找到全部正解、一題找到部分正解、一題查無結果，驗證 Recall@K 計算與分母。
- [x] 加入人工整理官方摘要、介面、JSON 評估輸出與啟動說明。
- [x] 執行 `python -m pytest -q` 與 `python -m compileall -q src app.py`。
- [x] 執行 `python scripts/evaluate.py`、本機 Streamlit health check，確認服務回覆 `ok`。

## 驗證紀錄（2026-10-21）

- 初次核心測試因模組尚未實作而失敗；實作後 13 項通過、2 項受系統暫存路徑權限影響，改用專案內暫存路徑後通過。
- 獨立審查發現公司名稱單獨查詢與過長 CSV 儲存格錯誤處理問題；新增重現測試並修正。
- 全部 23 項測試通過，包含 2 項 Streamlit AppTest；Python 語法編譯成功。
- 7 題 smoke 評估包含 5 題可回答、2 題無證據；Recall@3 與查無結果正確率均為 1.0。此題組為開發範例，不是獨立研究評估。
- 未安裝新套件、未呼叫外部模型、未變更原有 PNG；原目錄無 Git repository，因此沒有提交紀錄。

