# 企業請假與出勤制度助理

單一公司的精簡 HR RAG 專題，目標服務員工、主管、人資，依身分與文件權限回答請假、出勤制度及作業流程，提供可核對的來源與生效日期。

> **更新日期：2026-10-22。企業展示版已改為對話窗，全套 253 項離線／mock 測試通過，並完成瀏覽器上傳驗收；代表性使用者與真實模型驗收仍待完成。**
> 企業入口為 `enterprise_app.py`；原版公開資料入口 `app.py` 保留。示範制度全是虛構，不包含個人假單或出勤資料。
> `main` 保留可執行原版基準；企業版開發分支為 `codex/enterprise-mvp`。角色權限完成狀態以下方進度與測試為準。

Repository：<https://github.com/mina201199/hr-leave-attendance-rag>（Private）。

## 1. 已確認範圍與第一版邊界

- **已確認**：精簡企業版、單一公司、請假與出勤制度、員工／主管／人資三角色與權限。
- **本期採用**：保留 Streamlit、Python 與 SQLite，沿用現有 RAG 模組；先完成角色與授權流程，再評估是否需要 FastAPI。
- **資料來源已確認（2026-10-22）**：先採用明確標示「虛構、僅供展示」的公司制度開發與測試，不使用真實公司內規或個資。
- **現有模型選型保持不變**：回答 `gpt-6-luna`，embedding `text-embedding-3-small`，BM25 + 向量 + RRF；不預先加入 Reranker。
- **本期不做**：個人薪資、剩餘假別、即時出勤紀錄、實際送出／核准申請、薪資計算、跨公司多租戶、企業 SSO、OCR、PDF／Word 解析及正式對外部署。
- **實際公司資料上雲前**：先確認公司允許送至外部模型的資料範圍。登入權限只限制誰可以查詢，不等於公司同意把內規傳給 OpenAI。展示階段先使用公開與虛構資料。

## 2. 要解決的工作情境

| 對象 | 問題 | 第一版服務 | 展示問題 |
|---|---|---|---|
| 員工 | 找不到規定、申請順序、表單與承辦窗口 | 查可閱讀的公司制度與官方資料 | 臨時請假要通知誰，之後要補什麼資料？ |
| 主管 | 不清楚審核項目、例外與轉交條件 | 查主管審核指引，列出需確認事項 | 員工漏打卡，主管要確認哪些資料？ |
| 人資 | 重複答覆、制度版本不一致 | 查內部 SOP、維護並發布知識文件、處理回饋 | 新版辦法何時生效，舊版何時停止適用？ |

這些是待驗證的需求假設。驗收時應記錄代表性使用者完成任務的結果，不以資料筆數或模型名稱代替企業價值。

## 3. 角色與權限設計

身分由後端驗證的帳號取得，不能靠角色下拉選單、提問文字或請求欄位自稱主管／人資。

| 資料或操作 | employee | manager | hr |
|---|---|---|---|
| 已發布的一般員工制度與官方來源 | 可讀 | 可讀 | 可讀 |
| 主管審核指引 | 不可讀 | 可讀 | 文件明確授權才可讀 |
| 人資內部 SOP | 不可讀 | 不可讀 | 文件明確授權才可讀 |
| 本人的查詢紀錄與回饋 | 可讀寫自己的紀錄 | 可讀寫自己的紀錄 | 可讀寫自己的紀錄 |
| 其他人的問答紀錄 | 不可讀 | 不可讀 | 不因人資角色而自動取得 |
| 草稿預覽、匯入、發布、撤回、建索引 | 不可操作 | 不可操作 | 另外具有 `knowledge:manage` 能力的指定維護者 |

授權規則：

1. 內部文件缺少明確 ACL 時拒絕存取，不沿用現有 `allowedRoles=("public",)` 當作企業預設。
2. 先檢查帳號、角色、文件 ACL、發布狀態及生效日期，再檢索；授權與來源版本也要在生成前及顯示時重新確認。
3. 引用、原始文件、資料來源列表、紀錄與回饋入口都要檢查權限。受限文件不能先傳到前端再隱藏。
4. 帳號停用、角色變更、文件撤回後，舊 session／畫面結果不能繼續暴露已失去權限的內容。
5. 一般使用者的查詢授權與維護者的索引授權分開；不能用 HR 身分查詢就自動觸發全庫索引。
6. 未來若加入個人資料，另加入本人／部門／管理關係判斷，三角色不代表能讀所有個人紀錄。

### 角色可查與受限的問題範例

以下依目前 `data/company_demo/` 的虛構示範文件 ACL 整理。「受限」表示不能取得該專用文件，不是禁止輸入問題；若其他可讀文件涵蓋部分內容，仍可依那些來源回答。角色以後端登入身分為準，提問中自稱主管或人資不會升權。

| 問題範例 | 員工 employee | 主管 manager | 人資 hr | 對應文件 |
|---|---|---|---|---|
| 臨時請假要通知誰、補哪些資料？ | 可查 | 可查 | 可查 | `demo-emergency-leave` |
| 補打卡要填寫哪些說明？ | 可查 | 可查 | 可查 | `demo-attendance` |
| 主管收到申請後，要核對哪些事項與交接安排？ | 受限 | 可查 | 可查 | `demo-manager` |
| 主管遇到多人同時請假怎麼處理？ | 受限 | 可查 | 可查 | `demo-manager-overlap` |
| 主管退回申請時要註明什麼？ | 受限 | 可查 | 可查 | `demo-manager-return` |
| 人資收到制度疑義，文件未涵蓋時如何處理？ | 受限 | 受限 | 可查 | `demo-hr` |
| 人資收到打卡設備故障通報後，要核對哪些事項？ | 受限 | 受限 | 可查 | `demo-hr-device` |
| 人資發布新版制度前，要確認哪些項目？ | 受限 | 受限 | 可查 | `demo-hr-announcement` |

三角色都不支援查詢個人薪資、剩餘特休或個別員工出勤紀錄，也不能送出／核准真實假單、修改打卡紀錄，或把未發布草稿當成現行制度回答。人資仍須符合文件 ACL；發布、撤回等管理操作另外需要 `knowledge:manage`，不能由「可查發布流程」推論為「可執行發布」。

固定離線回歸測試位於 `tests/test_enterprise_role_access.py`，以 8 題 × 3 角色檢查來源清單、直接引用、檢索結果、生成上下文及回答；另檢查冒用角色文字、重用他人回答、紀錄與回饋越權。測試使用暫存資料庫、BM25 與離線來源摘錄，並封鎖網路連線，不呼叫付費模型或修改既有帳號／資料庫。

在已安裝專案測試依賴的 Python 環境執行：

```powershell
python -m pytest -q tests/test_enterprise_role_access.py
```

手動驗證時，以同一適用日期分別登入各角色，輸入上表問題並比對引用。例如「人資發布新版制度前，要確認哪些項目？」在員工／主管帳號下不得引用 `demo-hr-announcement`；人資帳號可取得該來源。這些是離線工程回歸檢查，不代表真實模型品質或代表性使用者驗收已完成。

## 4. 現有專案保留、修改與新增項目

| 處理方式 | 檔案／模組 | 原因與用途 |
|---|---|---|
| 保留原版入口 | `app.py`、`runtime.py`、`service.py`、`telemetry.py` | 原公開 RAG 可持續執行、比較與回復 |
| 沿用檢索核心 | `retrieval.py`、`hybrid.py` | BM25、cosine、RRF；企業服務先做授權再提供候選資料 |
| 保留原版上下文工具 | `processing.py`、`context.py` | 企業使用完整句子切片；企業上下文另保留文件與 ACL 來源範圍，避免文字去重混淆來源 |
| 保留 provider 與原評估 | `providers.py`、`generation.py`、`evaluation.py` | 原版提示、測試與模型選型維持基準；企業回答契約另建 |
| 保留公開資料 | `data/corpus/`、`data/seed/`、具名 `data/raw/`、歷史 `reports/` | 官方來源快照與原測試可在新電腦重建；企業資料庫不混入原版 |
| 擴充資料契約 | `models.py`、`ingestion.py` | 公司制度／主管指引／人資 SOP、ACL、維護者、生效日、發布狀態、外送許可與虛構標記 |
| 擴充版本管理 | `storage.py` | 版本內容不可變，發布狀態獨立保存；草稿不遮蔽現行版，撤回不復活舊版 |
| 小幅調整原版輸出 | `scripts/prepare_corpus.py` | 匯出時排除新欄位，維持原公開語料快照的位元組基準 |
| 新增身分與授權 | `auth.py`、`policy.py`、`scripts/manage_accounts.py` | 個別帳號、session 撤銷、集中閱讀權與維護能力 |
| 新增企業服務 | `enterprise.py`、`enterprise_generation.py` | 查詢、引用、本人紀錄、管理、企業回答格式及獨立 runtime |
| 新增企業入口 | `enterprise_app.py` | 三角色登入與制度查詢，原裁罰工具留在原版 |
| 新增企業資料與測試 | `data/company_demo/`、企業評估題組、`test_enterprise_*.py`、auth／policy 測試 | 虛構制度、越權防護、版本、雲端邊界與 UI 回歸 |
| 更新交接文件 | `README.md`、`AGENTS.md`、`docs/enterprise-*.md`、`.env.example` | 清楚區分實作完成、工程驗證、人工驗收及不在範圍內的功能 |

表內 Python 模組除入口與腳本外均位於 `src/labor_rag/`。Package 暫時保留 `labor_rag` 名稱，避免純改名造成不必要變動。實際完成狀態以下方勾選項目與測試紀錄為準。

## 5. 實作順序與完成條件

每一階段先加入能重現需求的測試、確認失敗，再做最小修改並重跑相關測試；完成後提交獨立 commit。

### 步驟 0：保存原版與建立交接基準

- [x] 保留原版操作說明到 `docs/legacy-public-rag.md`。
- [x] 整理企業範圍、檔案調整、權限矩陣與實作順序。
- [x] Git 忽略金鑰、執行資料庫、帳密、個人／公司私有資料與測試暫存。
- [x] 納入特定官方原始快照，讓既有重建測試不依賴這台電腦。
- [x] 建立並推送 Private repo，原版基準為 `41ee534`；本機與遠端 SHA 一致。
- [x] 由 Git 暫存內容匯出全新副本，70 項測試通過、47 個原始快照雜湊一致；無金鑰與執行資料庫。
- 驗收：只使用已提交檔案即可執行既有測試；遠端 repo 可取得 README 與程式碼。

### 步驟 1：固定情境、資料契約與測試題目

- [x] 使用者確認先以虛構公司示範資料開發及測試；不當作真實法規與企業制度。
- [x] 建立 5 份虛構文件、6 個版本（含草稿與新舊版），標示來源類型、ACL、狀態、維護者、生效日期。
- [x] 擴充資料契約及發布狀態；獨立企業匯入腳本拒絕原版資料庫；原版語料與快照保持不變。
- [x] 實作前固定 6 題開發、5 題工程回歸題組，涵蓋角色、日期與無資料；衝突格式使用 mock 驗證，真實模型的衝突判斷仍待測。兩組不是獨立人類保留集。
- 驗收：缺 ACL、日期錯誤、未支援來源類型的資料被拒絕；同版本修改被拒絕；新舊版切換保留歷史。

### 步驟 2：完成登入與集中授權

- [x] 新增 `auth.py`、`policy.py` 與帳號管理入口；三角色採個別帳號，不寫入固定密碼。
- [x] 定義可信 `Principal`（帳號 ID、角色、明確能力）及 session 生命周期；每次操作重新確認帳號狀態。
- [x] 將 `knowledge:manage` 與閱讀權分離，建立角色與文件矩陣測試。
- 驗收：錯誤密碼、到期 session、停用帳號不能操作；偽造角色不會提權；員工與主管不可發布資料。

### 步驟 3：把授權串到整條 RAG 管線

- [x] 新增企業服務，重用既有檢索核心；以後端驗證的身分、日期與已發布版本選來源，保留原版服務。
- [x] 分開維護者索引路徑與使用者查詢路徑；只有獲准外送的公開／虛構來源可進入現有 OpenAI embedding。
- [x] 新增受控引用讀取；來源頁、引用、紀錄、回饋、父段落與快取都套用同一授權政策。
- 驗收：用假的 embedder／generator 記錄輸入，證明受限來源未進入無權使用者的上下文；直接猜引用 ID、跨帳號讀紀錄、文件撤回與角色降級都不洩漏內容。

### 步驟 4：完成請假與出勤回答規則

- [x] 新增企業生成提示與回應契約：簡短結論、適用條件、有依據的申請／審核步驟、表單／窗口、引用及版本。
- [x] 企業提示區分官方來源與內規，提供需補條件／衝突回應格式；格式已 mock 驗證，模型能否正確辨識仍待真實測試。
- [x] 保留完整原文對照；不以引用 ID 有效宣稱答案語意正確。
- 驗收：不編造窗口／表單，不混用一般規則與例外，不推算個人剩餘假別，不將一般背景當作特殊流程答案。

### 步驟 5：完成三角色介面與最小管理流程

- [x] 新增 `enterprise_app.py`：登入、請假／出勤查詢、受控來源與本人紀錄；保留原 `app.py`。
- [x] 指定維護者可預覽、匯入草稿、發布／撤回及指定日期建索引，後端逐操作驗權。
- [x] 16 項 AppTest 覆蓋三角色、登出、停用、實際主管降為員工、舊對話／紀錄／回饋拒絕、管理、多輪日期與對話上限。
- [x] 對話窗保留本次登入最近 10 輪，每輪附引用及日期；登出清除，失去來源權限時移除整輪問題與答案。
- 驗收界線：AppTest 無法互動操作 file_uploader；已另以真實瀏覽器完成選檔、大小、編碼及發布流程驗收，見 [瀏覽器驗收紀錄](docs/enterprise-browser-acceptance-20261022.md)。

### 步驟 6：完整驗證與待辦

- [x] 對話版全套 253 項離線／mock 測試通過，包含原版回歸；對話介面改版前程式提交 `e750ab9` 的開發題組 6／6、工程回歸 5／5。
- [x] 越權與跨帳號洩漏為不通過條件；測試與結果見 [驗證紀錄](docs/enterprise-validation.md)。
- [x] 文件列明已知限制，保留原模型與介面選擇，不以檢索命中取代語意品質。
- [x] 全新 Git 匯出副本驗證與功能分支同步；129 個已追蹤檔案的交接掃描未發現金鑰或執行資料庫，原版資料與歷史報告保持不變。
- [x] 真實瀏覽器完成上傳、錯誤編碼、空檔、格式／ACL 拒絕、預覽換檔、發布／撤回；恰好 1 MiB 可預覽，超過 1 byte 被拒絕。
- [ ] 人工核對三角色操作與流程需求，記錄成功／失敗、耗時及人工查找基準。
- [ ] 另行核准外送與付費預算後，驗證真實模型的答案、引用支持、條件／例外、追問與衝突處理。

後兩項不因自動測試或工具代操作通過而自動算完成；詳見 [人工驗收清單](docs/enterprise-manual-acceptance.md)。

## 6. 換電腦下載與接續

需要 Git、Python 3.11 以上與可讀取此 Private repo 的 GitHub 帳號。以下使用 PowerShell。企業版放在 `codex/enterprise-mvp`；`main` 保留原版基準。

第一次下載：

```powershell
git clone --branch codex/enterprise-mvp https://github.com/mina201199/hr-leave-attendance-rag.git
cd hr-leave-attendance-rag
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" tzdata
.\.venv\Scripts\python.exe -m pytest -q
```

已下載過：

```powershell
cd hr-leave-attendance-rag
git status --short
git fetch origin
git switch codex/enterprise-mvp
git pull --ff-only
```

有未提交變更時先保存自己的工作，不用 `reset --hard` 或覆寫檔案強行同步。Python 套件目前沒有跨平台鎖定檔，新電腦應重新測試；Windows 安裝 `tzdata` 可提供 `Asia/Taipei` 時區資料。

## 7. 免費啟動企業展示版

以下環境變數只影響目前 PowerShell，會覆蓋本機 `.env` 的雲端設定，不需要 API key。

```powershell
$env:RAG_PROVIDER = "extractive"
$env:RAG_RETRIEVAL = "bm25"
$env:RAG_RERANKER_PATH = ""
$env:OPENAI_API_KEY = ""
$env:RAG_ENTERPRISE_DATA_DIR = "data/index/enterprise"
.\.venv\Scripts\python.exe -X utf8 scripts/prepare_enterprise.py
.\.venv\Scripts\python.exe -X utf8 scripts/prepare_enterprise.py --import
.\.venv\Scripts\python.exe -X utf8 scripts/manage_accounts.py create --user my.employee --role employee
.\.venv\Scripts\python.exe -X utf8 scripts/manage_accounts.py create --user my.manager --role manager
.\.venv\Scripts\python.exe -X utf8 scripts/manage_accounts.py create --user my.hr --role hr --manage
.\.venv\Scripts\python.exe -m streamlit run enterprise_app.py
```

帳號名稱可自行替換；每個 create 命令會以隱藏輸入請你設定並確認密碼（12–256 字元）。沒有預設帳密，不會自動建立帳號。第二次啟動可省略 create；重複匯入示範檔不會重設撤回狀態。帳號庫與索引不隨 Git 同步，所以新電腦需要自行建立本機帳號。

更新程式後，重新執行 `scripts/prepare_enterprise.py --import` 可補入新增示範資料；若使用自訂展示目錄，請加上 `--data-dir <原目錄>`。匯入範圍包含原有 `knowledge.jsonl` 與新增 `scenarios.jsonl`，共 15 份文件、16 個版本（含未來版本與草稿）。新增 10 個情境涵蓋臨時請假、變更／取消、交接、設備故障、外出洽公、出勤疑義，以及主管、人資處理指引；全部為虛構且禁止外送。

開啟 <http://127.0.0.1:8501>。登入後依序試：

在底部固定的對話輸入框輸入完整問題並按 Enter，系統直接搜尋目前角色可讀的請假與出勤制度，不必先選主題。自動依台灣時間的今天查詢有效制度；每輪保留當時日期與引用，舊對話不會跟著換日期。「＋ 新對話」清空本次畫面並回到聊天頁，「登出」會清除身分與對話。最近 10 輪保留在 session 記憶體，原始問答不寫入資料庫；目前逐題檢索，不會自動推論上一題條件。

「常用問題」提供可直接點選的問題，依目前角色可讀且於今天有效的來源顯示；來源撤回後選項一併移除。按鈕和自行輸入使用相同的查詢與授權流程，不會因重新整理重複送出。

「常用問題」固定放在底部輸入框正上方；新對話預設展開，已有對話時可點開選題。

2026-10-27 依使用者確認改為類似 ChatGPT 的對話版面：舊訊息在上、新訊息在下，問題靠右，回答靠左，輸入框固定底部。左側選單切換「制度查詢」「資料來源」「我的紀錄」，具維護能力的人資另有「維護管理」。回答的完整來源集中於「查看引用」，展示標示與設定移到側欄；沒有新增永久聊天列表或跨輪推論。

每輪對話只顯示問題、回答與「查看引用」，不顯示額外說明或回饋按鈕。該輪適用日期移至預設收合的「查看引用」內，與制度生效日期、完整原文及識別碼一起查看；正文隱藏服務附加的 `[demo-…]` 來源代碼，保留重點句中的流程條件。側欄保留虛構展示標示，登入頁也有提示。

離線示範回答改為一段重點：從最高相關來源挑選與問題最接近的完整句子，連同可辨識的前提與相鄰例外呈現，不再連貼多份制度。引用僅顯示畫面答覆實際使用的來源，並保留該來源完整原文；不把其他檢索候選列入引用。未精簡且確實使用多份來源的回答仍保留多份引用。這是本機文字比對，未使用生成模型，不保證理解所有複合問題或任意制度的條件；無來源、衝突等狀態及非示範來源不套用這項簡化。

| 帳號 | 問題 | 核對重點 |
|---|---|---|
| 員工 | 補打卡要填寫哪些說明？ | 一般出勤制度；無主管、人資專用來源 |
| 主管 | 主管如何確認交接安排？ | 主管指引；無人資 SOP |
| 人資 | 制度疑義如何確認版本與生效日？ | 人資 SOP；只有具維護能力者出現管理功能 |

查詢頁不再提供日期選擇；請假流程 v1／v2 的日期切換由離線測試覆蓋，人資建立索引的日期選擇仍保留。離線模式是**來源摘錄**，不冒充模型理解後的回答。完整操作情境見 [人工驗收清單](docs/enterprise-manual-acceptance.md)。

帳號變更範例：

```powershell
.\.venv\Scripts\python.exe scripts/manage_accounts.py set-password --user my.employee
.\.venv\Scripts\python.exe scripts/manage_accounts.py set-role --user my.hr --role hr
.\.venv\Scripts\python.exe scripts/manage_accounts.py disable --user my.employee
.\.venv\Scripts\python.exe scripts/manage_accounts.py enable --user my.employee
```

`set-role` 未加 `--manage` 會移除維護能力。角色、能力、密碼或啟用狀態改變都撤銷既有登入；session 最長八小時。所有管理指令可用 `--data-dir` 指定企業目錄，須與企業程式相同。這是有本機存取權的管理工具，不提供遠端帳號管理端點。

### 資料管理與雲端模式

企業上傳上限為 **1 MiB（1,048,576 bytes）**；原版傳輸設定保留封裝餘裕，企業入口另做檔案大小驗證。不要把 `server.maxUploadSize` 改為 1，否則剛好達上限的檔案可能因傳輸封裝超限而失敗。

維護者匯入 JSONL 先預覽、再存成草稿；發布與撤回分開操作。內規必須有明確 ACL、維護者、生效日及 `internal://<documentId>`。同版本內容不能覆寫；修訂要用新版本。未發布草稿不能直接撤回，暫不使用時維持草稿；只有已發布版本可撤回。引用由後端驗權讀取，內部識別碼不是公開下載網址。建立向量索引可指定適用日期，供歷史或未來制度查詢使用；一般查詢不會自動建索引。

示範語料每筆 `externalProcessingAllowed=false`，因此預設不允許 OpenAI embedding 或生成。切換雲端模式不會繞過這個限制。真正需要雲端測試時，先確認資料外送許可及測試預算，另以新版本記錄許可後再執行；本次沒有呼叫付費 API。模型仍為 `gpt-6-luna`／`text-embedding-3-small`，未啟用 Reranker。

## 8. 原版基準與 Git 資料界線

原版 `app.py` 保留，啟動與公開語料重建見 [原版操作說明](docs/legacy-public-rag.md)。不要把原版 `prepare_corpus.py --import` 指向企業資料庫。企業預設目錄為 `data/index/enterprise`，原版預設為 `data/index`。

- **同步**：程式、測試、設定範例、虛構制度、實作計畫、原版整理語料、具名官方快照與具名歷史公開報告。
- **不會同步**：`.env`、金鑰、真實內規與個資、帳號／session 資料庫、本機索引、虛擬環境、暫存與 log。
- Private repo 同樣不可提交機密。`data/company_demo/` 全是虛構制度，不能拿來當真實公司辦法或法律依據。
- 特定歷史 API 報告供 `check_corpus.py` 累計費用預留，既有約 US$2.95／US$3。不得刪除、改名或提高上限來繞過預算。
- 舊報告有原電腦的歷史路徑，並非新電腦必要路徑；舊 `--resume` 不保證跨電腦可用。
- 原版 18 題 live 成績使用較早提示；最終 `labor-grounded-v3` 只複測特休兩題，不能當作企業版成績。
- 索引與網頁查詢沒有全站費用上限鎖，腳本預留額也不是 OpenAI 帳戶費用上限。

## 9. 下一次接續開發

先讀本 README、`AGENTS.md` 及 [企業設計](docs/enterprise-design.md)，跑離線測試，再依上方未勾選項目接續。分階段計畫在 [2026-10-22 企業計畫](docs/superpowers/plans/2026-10-22-enterprise-mvp.md)；其他 `2026-10-21` 計畫是原版歷史。

可把以下內容交給協作工具：

> 請先確認目前在 codex/enterprise-mvp，讀 README、AGENTS 與企業設計，執行離線測試，再列出尚未完成的驗收項目。保留原版程式與資料，只使用明確標示虛構的制度，不新增固定帳密、不提交執行資料庫，不更換模型或啟用 Reranker。未另行核准付費預算及資料外送前，只跑離線／mock。不要把工程回歸成績宣稱為真實模型品質或企業效率改善。

## 10. 參考資料

- [瀏覽器上傳驗收與對話介面](docs/enterprise-browser-acceptance-20261022.md)
- [企業工程驗證紀錄](docs/enterprise-validation.md)
- [企業展示版人工驗收清單](docs/enterprise-manual-acceptance.md)
- [原版操作說明](docs/legacy-public-rag.md)
- [原版架構與能力界線](docs/architecture.md)
- [120 筆公開語料來源與驗證紀錄](docs/corpus-120.md)
- [原版特休條件修正報告](reports/annual-condition-validation-20261021.json)
- [原版公開語料驗證報告](reports/corpus-120-validation-20261021.json)

本專案以本機開發與展示為用途。正式內部部署、企業 SSO、HTTPS、備份與維運監控尚不在本期完成範圍。
