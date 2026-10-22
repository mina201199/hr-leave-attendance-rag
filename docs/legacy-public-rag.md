> 原版封存說明（2026-10-21）：以下命令均以專案根目錄為工作目錄。企業版最新方向請見 [README](../README.md)。特定官方快照及既有公開評估報告現已納入版本控制，忽略規則以根目錄 `.gitignore` 為準。

# 打工權益問答與勞動裁罰紀錄檢索

依 `企業級RAG_教學簡報.html` 的六層架構建立的本機專題：資料來源 → 處理 → 版本與向量儲存 → 混合檢索 → OpenAI 來源問答 → 介面、追蹤與評估。

已選定 GPT-6 Luna 回答、text-embedding-3-small 向量檢索，第一版不啟用 Reranker。`.env` 與範例已設為 OpenAI 混合檢索；未填金鑰時會提示缺少 `OPENAI_API_KEY`，不會自動切換模式。2026-10-21 語料包含 40 筆官方問答與 80 件臺北市裁罰，共 120 筆獨立資料、124 個檢索片段。原先 6 筆摘要保留作開發測試，正式資料庫匯入擴充版後停用舊摘要。

## 120 筆語料與重現

資料說明見 [語料來源與篩選紀錄](corpus-120.md)，逐筆來源清單在 `data/corpus/2026-10-21/manifest.json`，整理後可匯入檔在 `data/corpus/2026-10-21/knowledge.jsonl`，原始官方 HTML 與 CSV 保留在 `data/raw/expansion-20261021/`。

```powershell
python -X utf8 scripts/prepare_corpus.py --import
python -X utf8 scripts/check_corpus.py
python -X utf8 scripts/check_corpus.py --live
```

前兩個命令不呼叫 API。匯入會先備份本機資料庫、停用舊摘要並保留歷史，可重複執行而不重複新增資料。`--live` 才建立缺少的向量並執行 18 題付費測試。測試固定已核准模型，採每次 US$0.05 的保守費用預留，連同保留在 `reports/` 的先前實測預留合計不得超過 US$3；報告刪除會失去這段歷史。這項保護只約束此測試腳本，不是網站操作或 OpenAI 帳戶的費用上限。報告保留公開測試問題、答案與 token 用量，不包含金鑰。

介面側欄應顯示「120 筆文件／案件，124 個檢索片段」。可試問「公司發薪時一定要給薪資明細嗎？」；公司範例可填完整名稱「剛剛股份有限公司」，主題選「裁罰」，問題填「請列出處分字號、違規內容及罰鍰金額」。案件來自歷史公告，不能推論目前仍違規。

## 啟動與驗證

在此專案資料夾開啟 PowerShell：

```powershell
python -m pip install -e ".[dev]"
python -m streamlit run app.py
```

開啟 http://127.0.0.1:8501 。現有環境已具備主要依賴；若服務已啟動，只要重新整理。停止服務請在其終端機按 Ctrl+C。

1. 依下節填入金鑰並建立索引後，輸入「打破杯子老闆可以扣薪嗎？」：應顯示依來源整理的回答、引用 ID、官方連結及查詢紀錄 ID。
2. 輸入「火星火山地質」：預期拒答／指出來源不足。這是驗證目標，若仍回答，請記錄為評估失敗，不能當作正常結果。
3. 點「有幫助」，到「查詢紀錄」查看回饋、耗時與來源 ID；不儲存問題或答案原文。
4. 從側欄連結取得官方裁罰 CSV，上傳後按「確認匯入並儲存」。資料會保留於本機；可用完整公司名稱查詢。系統不驗證上傳內容真偽。
5. 「檢索評估」執行固定 BM25 題組；比較混合檢索請使用下方命令。

## 啟用 OpenAI

`.env.example` 是設定範例；本機 `.env` 已預留空白金鑰。若在其他電腦尚無 `.env`，只在不存在時複製：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

到 [OpenAI API keys](https://platform.openai.com/api-keys) 建立自己的金鑰。在本機文字編輯器開啟 `.env`，只需在 `OPENAI_API_KEY=` 後貼上金鑰並儲存，其餘選型設定已完成。不要把真實金鑰貼到對話、程式碼或報告，也不要填進 `.env.example`。可在專案 PowerShell 執行 `notepad .env`；確認檔名仍為 `.env`，不是 `.env.txt`。官方步驟見 [API quickstart](https://developers.openai.com/api/docs/quickstart)。

```dotenv
OPENAI_API_KEY=填入你自己的金鑰
OPENAI_CHAT_MODEL=gpt-6-luna
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
RAG_PROVIDER=openai
RAG_RETRIEVAL=hybrid
RAG_RERANKER_PATH=
```

以上為使用者已確認的選型，後續更換前須先討論。需確認帳號有權使用所選模型。官方規格：[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small)。程式未額外指定推理強度，沿用 GPT-6 Luna 的 API 預設 medium；後續若調整速度與費用，先討論再變更。

重新整理頁面，按側欄「建立／更新 OpenAI 向量索引」，完成後即可查詢。沒有索引時會停用混合檢索送出按鈕，不會偷偷改用其他模式。索引只補缺少的目前公開片段；內容、版本或 embedding 模型改變後，需再執行一次。

驗證時側欄應顯示「回答：OpenAI 來源問答」「檢索：BM25 + 向量 + RRF」，並標示未啟用 CrossEncoder。匯入 120 筆擴充語料後首次建立 124 個向量；若已有相同模型與版本的索引，新增 0 個也是正常。查詢完成後，在「查詢紀錄」展開詳細紀錄，確認 `model` 為 `gpt-6-luna`、存在引用 ID 與 API 回報的用量。

常見錯誤：缺少金鑰先確認 `.env` 檔名及儲存；HTTP 401 檢查金鑰；403／404 檢查模型或專案權限；429 檢查 API 額度與速率限制。不要把金鑰貼進錯誤回報。如果系統環境變數已有同名設定，它會優先於 `.env`，請檢查啟動程式的終端機環境。

建立索引會將公開來源文字送至 OpenAI；查詢會傳送問題，生成會傳送問題及選中的來源段落，並產生 API 費用。Responses 使用 `store=false`，這不表示資料不出本機，也不是零保留承諾。模型錯誤會直接顯示錯誤，不以模型記憶補答。

可將 `RAG_PROVIDER=openai` 搭配 `RAG_RETRIEVAL=bm25`，單獨比較「關鍵字檢索 + LLM」；`extractive` 搭配 `hybrid` 可比較「混合檢索 + 來源摘錄」。只有 `extractive + bm25` 完全不需 API。

## 六層對照

| 簡報層級 | 本專題實作 |
|---|---|
| 資料來源 | 官方問答／摘要 JSONL、臺北市裁罰 CSV，保留原始檔、來源網址與日期 |
| 資料處理 | 欄位驗證、完整句子切片、句子重疊與父段落全文 |
| 儲存與索引 | SQLite 中繼資料、歷史版本、啟停與有效日期；獨立向量表及內容雜湊 |
| 檢索 | 權限與有效版前置篩選、BM25、cosine、RRF；可選本機 CrossEncoder；父段落去重及內容預算 |
| 回答生成 | OpenAI 結構化重點與引用、無證據拒答、引用 ID 驗證；離線來源摘錄 |
| 應用與治理 | Streamlit、持久匯入、查詢紀錄、回饋、檢索評估、人工答案評分欄位 |

完整資料流、與四模組分工的對照及能力界線見 `docs/architecture.md`。SQLite 向量表採逐筆精確 cosine，適合小型專題；不是大型 ANN 向量服務。未實作企業登入、多租戶、OCR、PDF/Word 解析、線上監控告警與正式部署。

## 裁罰資料與版本維護

CSV 使用 UTF-8／UTF-8 BOM。必要欄位為 `事業單位或事業主之名稱`（也支援 `事業單位名稱`、`事業單位名稱/自然人姓名`）及 `違反法規內容`。保留可選的 `公告日期`、`處分日期`、`處分字號`、`違反勞動基準法條款`、`罰鍰金額`、`備註`。

來源：[臺北市違反勞動基準法公布總表](https://data.taipei/dataset/detail?id=23630879-4926-4877-a48a-a0ae6cc2f7d5)。公司名稱採完全相符；查無紀錄不代表沒有違規。有處分字號的案件會依機關、公司、字號核對，新舊匯入方式的相同案件與公告內容不重複儲存。若同案公告內容不同，會拒絕整批匯入，需核對後使用原文件 ID 的新版本 JSONL 更新，不能直接覆蓋歷史。

```powershell
python scripts/manage.py status
python scripts/manage.py import "你的官方裁罰檔.csv"
python scripts/manage.py import "你的已審核摘要.jsonl"
python scripts/manage.py index
python scripts/manage.py deactivate 文件ID
python scripts/manage.py activate 文件ID
```

`index` 會呼叫 OpenAI 並計費。維護命令是本機管理工具，不能當作多人權限驗證。

JSONL 契約以 `data/seed/official_faq.jsonl` 為例。每筆還可填 `version`（預設 `1`）、`active`、`allowedRoles`（預設 `["public"]`）、`effectiveFrom`、`effectiveTo`（ISO 日期）。同文件的新版本需一併匯入所有片段；版本名稱是識別碼，版本先後依匯入順序，不做字串排序。未來版本生效前仍選現行版本；新版本到期後不回復被取代的舊規則。重複匯入相同內容不會復活停用資料。

更新 JSONL 內容需提升 `version`，舊版保留供稽核。`sourceUpdatedAt` 是來源頁更新日，不能代替法律的生效日。父段落超過內容預算時顯示明確提示並停止生成，請按完整條款重新整理來源，而非刪掉例外或直接截斷。

## 評估與測試

```powershell
python -m pytest -q
python scripts/evaluate.py --answers
python scripts/evaluate.py --mode configured --answers
python -m compileall -q src app.py scripts
```

第一個評估命令使用固定 FAQ 與 BM25，完全離線；第二個使用 `.env` 與目前知識庫，雲端設定會發送 API 請求。可用 `--cases 路徑 --top-k 3` 指定獨立題組。`--answers` 會另外產生回答並保留於報告，這與預設不留原文的操作紀錄不同。

報告寫至 `reports/smoke-evaluation.json` 或 `reports/configured-evaluation.json`。記錄 Recall@K、Precision@K（分母固定 K）、MRR、查無結果正確率與耗時。前三個指標只平均有相關來源的題目；無答案題另計。每題的 `manualReview` 預留正確性、忠實度、引用支持度與備註，不自動填上虛假的品質分數或費用。

隨附 7 題仍是原始摘要的開發檢查，介面「檢索評估」使用這組固定基準，不代表目前 120 筆語料。擴充語料改用 `scripts/check_corpus.py` 與 `data/evaluation/corpus-120.jsonl` 的 18 題，含 12 題權益問答、4 題指定公司查詢、2 題無關問題。來源命中與引用命中不等同答案正確性；人工審查另行記錄。這些都是開發檢查，不是正式研究成績；正式專題仍應另建獨立測試集。

## 可選重排序

```powershell
python -m pip install -e ".[rerank]"
```

自行準備可信任且適合中文的 CrossEncoder 模型資料夾，將絕對路徑填入 `RAG_RERANKER_PATH`。程式只載入本機模型，不自動下載或啟用遠端程式碼。未設定時介面會標示未啟用 CrossEncoder；RRF 是名次融合，不能冒稱語意重排序。

## 主要檔案與資料位置

`app.py` 為介面；`src/labor_rag/` 包含六層模組；`scripts/manage.py` 維護知識庫；`scripts/evaluate.py` 評估；`tests/` 為回歸測試。

預設資料庫在 `data/index/knowledge.db`、紀錄在 `data/index/traces.db`。`.gitignore` 已排除 `.env`、索引、原始資料、報告及測試暫存檔。不要任意刪除資料庫；備份或搬移時先停止服務。現有原始 PNG 架構圖與 Downloads 簡報均未修改。
