import csv
import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .models import DocumentChunk


PENALTY_SOURCE_URL = "https://data.gov.taipei/dataset/detail?id=23630879-4926-4877-a48a-a0ae6cc2f7d5"
CATEGORIES = ("薪資", "工時", "休假", "裁罰")


def validateChunk(chunk: DocumentChunk) -> None:
  for fieldName in ("chunkId", "documentId", "title", "text", "publisher", "collectedAt", "version"):
    value = getattr(chunk, fieldName)
    if not isinstance(value, str) or not value.strip():
      raise ValueError(f"{fieldName} 不可為空白")
  if chunk.category not in CATEGORIES:
    raise ValueError("不支援的資料類別")
  if chunk.sourceType not in ("官方摘要", "官方問答", "裁罰紀錄"):
    raise ValueError("不支援的來源類型")
  parsedUrl = urlparse(chunk.sourceUrl)
  if parsedUrl.scheme != "https" or not parsedUrl.hostname:
    raise ValueError("來源網址必須為有效 HTTPS 網址")
  if not isinstance(chunk.active, bool):
    raise ValueError("active 必須為布林值")
  if not isinstance(chunk.allowedRoles, (tuple, list)) or not chunk.allowedRoles or any(not isinstance(role, str) or not role.strip() for role in chunk.allowedRoles):
    raise ValueError("allowedRoles 必須包含有效角色")
  for value in (chunk.effectiveFrom, chunk.effectiveTo):
    if value is not None and (not isinstance(value, str) or date.fromisoformat(value).isoformat() != value):
      raise ValueError("有效日期必須為 YYYY-MM-DD")
  if chunk.effectiveFrom and chunk.effectiveTo and chunk.effectiveFrom > chunk.effectiveTo:
    raise ValueError("有效日期起訖顛倒")
  if (chunk.parentId is None) != (chunk.parentText is None):
    raise ValueError("父片段 ID 與全文必須一起提供")


def loadChunks(sourcePath: str | Path) -> list[DocumentChunk]:
  chunks = []
  knownIds = set()
  with Path(sourcePath).open(encoding="utf-8-sig") as sourceFile:
    for lineNumber, line in enumerate(sourceFile, 1):
      if not line.strip():
        continue
      try:
        chunk = DocumentChunk(**json.loads(line))
        validateChunk(chunk)
        if chunk.chunkId in knownIds:
          raise ValueError(f"重複的 chunkId：{chunk.chunkId}")
        knownIds.add(chunk.chunkId)
        chunks.append(chunk)
      except (ValueError, TypeError) as error:
        raise ValueError(f"第 {lineNumber} 行資料無效：{error}") from error
  return chunks


def parsePenaltyCsv(csvText: str) -> list[DocumentChunk]:
  reader = csv.DictReader(io.StringIO(csvText.lstrip("\ufeff")))
  try:
    if not reader.fieldnames:
      raise ValueError("CSV 缺少欄位標題")
    reader.fieldnames = [name.strip() for name in reader.fieldnames]
    rows = list(reader)
  except csv.Error as error:
    raise ValueError(f"CSV 第 {reader.line_num} 行附近無法解析，請確認欄位長度及格式：{error}") from error
  companyHeaders = ("事業單位或事業主之名稱", "事業單位名稱", "事業單位名稱/自然人姓名")
  companyHeader = next((name for name in companyHeaders if name in reader.fieldnames), None)
  if not companyHeader or "違反法規內容" not in reader.fieldnames:
    raise ValueError("CSV 必須包含公司名稱欄位及「違反法規內容」欄位")
  chunks = []
  knownIds = set()
  collectedAt = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
  for lineNumber, row in enumerate(rows, 2):
    if None in row or any(value is None for value in row.values()):
      raise ValueError(f"CSV 第 {lineNumber} 行欄位數不一致")
    companyName = row[companyHeader].strip()
    violation = row["違反法規內容"].strip()
    if not companyName or not violation:
      raise ValueError(f"CSV 第 {lineNumber} 行公司名稱或違規內容為空白")
    fields = [f"事業單位：{companyName}"]
    for fieldName in ("公告日期", "處分日期", "處分字號", "違反勞動基準法條款", "違反法規內容", "罰鍰金額", "備註"):
      if row.get(fieldName, "").strip():
        fields.append(f"{fieldName}：{row[fieldName].strip()}")
    text = "\n".join(fields)
    recordId = "penalty-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
    if recordId in knownIds:
      continue
    knownIds.add(recordId)
    chunk = DocumentChunk(
      chunkId=recordId, documentId=recordId, title=f"{companyName}｜裁罰紀錄",
      text=text, sourceUrl=PENALTY_SOURCE_URL, publisher="臺北市政府勞動局",
      category="裁罰", sourceType="裁罰紀錄", collectedAt=collectedAt,
      companyName=companyName,
    )
    validateChunk(chunk)
    chunks.append(chunk)
  return chunks

