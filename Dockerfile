FROM python:3.12-slim

# 預設離線模式：不需要 API key、不產生雲端費用；改用 OpenAI 時在 docker run 以 -e 傳入，不寫進映像檔
ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1 \
  PYTHONUTF8=1 \
  PIP_NO_CACHE_DIR=1 \
  RAG_PROVIDER=extractive \
  RAG_RETRIEVAL=bm25 \
  RAG_DATA_DIR=/app/data/index

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY .streamlit ./.streamlit
COPY app.py ./
COPY scripts ./scripts
COPY data ./data

# 以非 root 身分執行，只開放索引目錄寫入，避免容器內程式改動程式碼與原始資料
RUN useradd --create-home --uid 10001 appuser \
  && mkdir -p /app/data/index \
  && chown appuser:appuser /app/data/index
USER appuser

# 建置時匯入 120 筆官方語料，容器啟動後即可查詢；這一步只讀本機檔案，不呼叫 API
RUN python scripts/prepare_corpus.py --import

EXPOSE 8501

# config.toml 綁定 127.0.0.1 只適合本機開發，容器內需綁定 0.0.0.0 才能從主機連線
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
