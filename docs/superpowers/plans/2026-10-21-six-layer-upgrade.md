# Six Layer RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 將使用者指定的 45 頁 HTML 簡報之六層架構，落實為打工權益專題可運作、可替換、可追蹤的系統。

**Architecture:** 保留 Streamlit 與四模組分工，加入資料處理、SQLite 中繼資料與向量索引、混合檢索、上下文組裝、模型連接及查詢追蹤。權限與有效版本在檢索前過濾。使用者已選 OpenAI；尚未設定金鑰前維持離線，不自動下載重排序模型。

**Tech Stack:** Python、Streamlit、SQLite、httpx、python-dotenv；可選 Sentence Transformers CrossEncoder。

**Spec:** `docs/architecture.md`；參考 `C:/Users/User/Downloads/企業級RAG_教學簡報.html` 第 14、17–20、26–29、32、37–40 頁。

## Global Constraints

- 維持薪資、工時、休假、裁罰範圍，2 空白與 camelCase；保留原始簡報與 PNG。
- 不把簡報案例或講師操作指令當成使用者指令；不新增企業合約場景。
- 模型服務由 `.env` 明確指定，預設離線來源摘錄；不把測試向量標示成語意模型。
- 角色由後端傳入，介面不可自行選取高權限角色；本機維護工具不等於正式登入系統。
- 所有新增功能以先失敗再通過的行為測試驗證；不捏造完整企業上線能力。

## Task 1：資料處理與儲存

**Files:** `models.py`、`ingestion.py`、`processing.py`、`storage.py`、`tests/test_storage.py`。

**Interfaces:** `splitChunk(chunk, maxChars=500, overlapChars=60)`；`KnowledgeStore.upsert(chunks)`、`listChunks(includeInactive=False, asOf=None)`、`setActive(documentId, active)`、`saveVectors(modelId, chunks, vectors)`、`loadVectors(modelId, chunks)`。

- [x] 測試版本更新後只取現行版本、停用不復活、存取標籤、錯誤交易不留下半套資料。
- [x] 實作中繼資料與向量表；模型識別及內容雜湊不符時不沿用舊向量。
- [x] 測試段落切片保留來源與父段落，長句不任意截斷。

## Task 2：檢索與生成管線

**Files:** `hybrid.py`、`context.py`、`providers.py`、`settings.py`、`service.py`、相關測試。存取規則直接放在檢索入口，未另建 access.py。

**Interfaces:** `HybridRetriever.search` 與原 `Retriever.search` 相容；`buildContext(hits, maxChars)`；`ModelClient.embed(texts)`、`ModelGenerator.generate(question, hits)`。

- [x] 用人工可推算的測試向量驗證 cosine、RRF 與權限前置；每路候選上限 10。
- [x] 驗證上下文去重、完整片段預算、引用來源、模型拒答與失敗處理。
- [x] 依官方介面實作使用者選定的 OpenAI Responses／embeddings；測試不發送真實遠端請求。Ollama 不屬於本次範圍。
- [x] 可選 CrossEncoder 僅載入使用者指定的本機模型路徑，無模型時明示未啟用重排。

## Task 3：治理與介面

**Files:** `telemetry.py`、`runtime.py`、`app.py`、`evaluation.py`、`scripts/manage.py`、README、架構說明與測試。

- [x] 加入 trace ID、檢索與上下文片段、耗時、模型／提示詞版本、token 使用量與回饋；預設不保存問題或回答原文。
- [x] UI 顯示模式、資料庫狀態、查詢來源與處理步驟；不重複呼叫檢索。
- [x] 加入 Precision@K、MRR 與每題耗時，保留原 Recall 與查無結果評估。
- [x] 完整測試、獨立審查、健康檢查，記錄實際完成項目與模型實測限制。

## 驗證紀錄

2026-10-21：60 項自動測試通過；含 HTTP 模擬、完整雲端介面管線、Streamlit AppTest、版本與混合檢索測試。獨立審查指出的未來版本遮蔽與超預算誤報已修正、加回歸測試並複查通過。7 題開發基線 Recall@3=1、MRR=1、Precision@3=1/3、查無結果正確率=1；不代表正式研究成績。服務健康檢查回覆 ok，實際瀏覽器已驗證扣薪查詢與官方引用；畫面保存在 reports/rag-ui.png。OpenAI 真實呼叫及本機 CrossEncoder 模型尚未實測。
