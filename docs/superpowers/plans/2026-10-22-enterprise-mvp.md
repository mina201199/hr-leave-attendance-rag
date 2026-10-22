# 精簡企業版實作計畫

> For agentic workers: use subagent-driven-development，按任務實作、測試與審查；不重跑已完成任務。

**Goal:** 完成單一公司請假與出勤制度的三角色展示版，同時保留原公開 RAG 基準。

**Architecture:** 既有模型及儲存契約向後相容擴充；新企業服務統一登入、授權、引用、紀錄及管理入口。Streamlit 企業入口獨立於原版。

**Tech Stack:** Python、Streamlit、SQLite、現有 OpenAI provider（本次不執行真實 API）。

**Spec:** `docs/enterprise-design.md`；需求與整體順序另見根目錄 `README.md`。

## Global Constraints

2 空白、camelCase、正體中文原因註解；既有模型不變；不覆寫原資料庫；公司資料全部虛構；不含固定帳密；後端以 token 驗證身分；逐任務 red/green 測試；每任務審查完成再進下一任務。

## Task 1：資料契約與虛構語料

- [x] 在 `tests/test_enterprise_corpus.py` 先測企業欄位、缺 ACL、內部引用、草稿、新舊版、撤回、原版持久資料相容與同版修改拒絕。
- [x] 修改 `models.py`、`ingestion.py`、`storage.py`；新增 `data/company_demo/` 與離線匯入腳本 `scripts/prepare_enterprise.py`。
- [x] `DocumentChunk` 新增 `publicationStatus`、`maintainerId`、`isDemo`、`externalProcessingAllowed`；企業來源為「公司制度」「主管指引」「人資SOP」。內部來源格式 `internal://<documentId>`。
- [x] `KnowledgeStore.setPublicationStatus(documentId, version, status)` 管理 draft/published/withdrawn；`listChunks()` 只選已生效發布版，includeInactive 供受控管理讀取；向後相容舊 public payload。
- [x] 執行企業資料測試與全部既有測試，修正後提交。

## Task 2：帳號與授權

- [x] 在 `tests/test_auth.py`、`tests/test_policy.py` 先測登入失敗、token 過期、角色變更失效、最小權限及擁有權。
- [x] 新增 `auth.py`：`Principal(userId, role, capabilities)`、`AuthStore(path)`；`createUser`、`login`、`authenticate`、`logout`、`setRole`、`setEnabled`、`setPassword`。
- [x] 新增 `policy.py`：`canRead(principal, chunk)`、`requireManage(principal)`；資料生效與發布狀態由 storage 與 service 共同查核。
- [x] 新增互動式 `scripts/manage_accounts.py`；不接受密碼明文命令列參數。
- [x] 執行 auth/policy 與全套測試，審查後提交。

## Task 3：企業服務、回答、引用與紀錄

- [x] 先測 session 驗證、跨角色內容隔離、父段落、直接引用、回饋擁有權、外送限制及查詢中撤回。
- [x] 新增 `enterprise.py` 企業服務與 runtime，外部操作一律接受 token；保留舊 service/runtime。
- [x] 查詢以授權結果建立 HybridRetriever，重新利用 buildContext；離線為完整來源摘錄，雲端仍使用既有選型但加企業提示與來源類型。
- [x] 企業紀錄獨立表保存擁有者和來源版本，不存原始問答；管理操作含預覽、匯入、發布／撤回、索引及受控來源清單。
- [x] 建立開發題組與離線評估腳本；鎖定參數後另執行未用於調參的回歸題組，明確揭露不是人類獨立保留集。
- [x] 執行服務及全套測試，審查後提交。

## Task 4：Streamlit 展示與完整交接

- [x] 以 AppTest 先測登入、登出、角色來源隔離、管理畫面、本人紀錄與 session 失效。
- [x] 新增 `enterprise_app.py`；主頁區分請假／出勤，登入前不顯示內規；session 只保留 token，每次重繪驗權。
- [x] 更新 README、AGENTS、架構、命令與完成狀態；保留待人工驗收／真實模型品質項目。
- [x] 全套 pytest、離線企業情境、乾淨 Git 匯出驗證、最終安全審查，推送功能分支供換機接續。

## 執行記錄

原版交接已完成：Private repo `mina201199/hr-leave-attendance-rag`，基準 `41ee534`，匯出副本 70 passed，47 原始快照雜湊一致。

企業程式提交 `e750ab9`：全新 Git 匯出副本 250 passed（52.48 秒），開發題組 6／6、工程回歸 5／5；129 檔交接掃描無金鑰或執行資料庫，原版資料與歷史報告保持不變。最終草稿撤回修正複審通過，功能分支已推送。瀏覽器上傳、三角色人工流程及真實模型品質仍待驗收，詳見 README 與 `docs/enterprise-validation.md`。
