import csv
import hashlib
import io
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from .ingestion import validateChunk
from .models import DocumentChunk


def parseOfficialFaq(html, item, collectedAt):
  soup = BeautifulSoup(html, "html.parser")
  if item["publisher"] == "臺北市政府勞動局":
    body = soup.select_one(".essay .p")
    match = re.search(r"資料更新[：:]\s*(\d{3})-(\d{2})-(\d{2})", soup.get_text(" ", strip=True))
    updatedAt = date(int(match[1]) + 1911, int(match[2]), int(match[3])).isoformat() if match else None
  else:
    body = None
    for row in soup.select("section.cp tr"):
      heading = row.find("th")
      if heading and heading.get_text(strip=True) == "答案":
        body = row.find("td")
        break
    update = soup.select_one(".publish_info_top")
    match = re.search(r"更新日期[：:]\s*(\d{4}-\d{2}-\d{2})", update.get_text() if update else "")
    updatedAt = date.fromisoformat(match[1]).isoformat() if match else None
  if body is None or not body.get_text(strip=True):
    raise ValueError(f"找不到官方問答正文：{item['rawFile']}")
  for tag in body.select("script, style"):
    tag.decompose()
  text = re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip()
  recordId = "official-" + Path(item["rawFile"]).stem
  chunk = DocumentChunk(chunkId=recordId, documentId=recordId,
    title=item["title"], text=text, sourceUrl=item["url"], publisher=item["publisher"],
    category=item["category"], sourceType="官方問答", collectedAt=collectedAt,
    sourceUpdatedAt=updatedAt, version=collectedAt)
  validateChunk(chunk)
  return chunk


def buildPenaltyCases(csvText, count, collectedAt):
  reader = csv.DictReader(io.StringIO(csvText.lstrip("\ufeff")))
  required = ("處分字號", "處分日期", "事業單位或事業主之名稱", "違反勞動基準法條款", "違反法規內容", "備註")
  if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
    raise ValueError("裁罰 CSV 欄位不完整")
  groups = defaultdict(list)
  for row in reader:
    if None in row or any(value is None for value in row.values()):
      raise ValueError("裁罰 CSV 欄位數不一致")
    row = {key: value.strip() for key, value in row.items() if key}
    if not all(row[key] for key in required[:3]):
      raise ValueError("裁罰案件缺少識別欄位")
    if row not in groups[row["處分字號"]]:
      groups[row["處分字號"]].append(row)
  selected = []
  for caseNumber, rows in groups.items():
    remarks = " ".join(row["備註"] for row in rows)
    if "撤銷" in remarks or not re.search(r"工讀|部分工時", remarks):
      continue
    if not any(re.search(r"第(?:2[1-8]|3[0-9]|4[0-3])條", row["違反勞動基準法條款"]) for row in rows):
      continue
    if len({(row["處分日期"], row["事業單位或事業主之名稱"]) for row in rows}) != 1:
      raise ValueError("同處分字號的公司或日期不一致")
    selected.append((caseNumber, rows))
  selected.sort(key=lambda pair: (pair[1][0]["處分日期"], pair[0]), reverse=True)
  if len(selected) < count:
    raise ValueError(f"合格裁罰案件不足：{len(selected)} / {count}")
  chunks = []
  fields = ("公告日期", "處分日期", "處分字號", "事業單位或事業主之名稱", "違反勞動基準法條款", "違反法規內容", "罰鍰金額", "備註")
  for caseNumber, rows in selected[:count]:
    companyName = rows[0]["事業單位或事業主之名稱"]
    parts = ["本筆為歷史裁罰公告，不能推論事業單位目前仍違規；日期為民國年月日。"]
    if len(rows) > 1:
      parts.append("同處分字號合併呈現各公告列，罰鍰金額保留原列，不自行加總。")
    for row in rows:
      parts.append("\n".join(f"{field}：{row[field]}" for field in fields if row.get(field)))
    recordId = "taipei-case-" + hashlib.sha256(caseNumber.encode("utf-8")).hexdigest()[:20]
    chunk = DocumentChunk(chunkId=recordId, documentId=recordId,
      title=f"{companyName}｜{caseNumber}｜裁罰紀錄", text="\n\n".join(parts),
      sourceUrl="https://data.taipei/dataset/detail?id=23630879-4926-4877-a48a-a0ae6cc2f7d5",
      publisher="臺北市政府勞動局", category="裁罰", sourceType="裁罰紀錄",
      collectedAt=collectedAt, companyName=companyName, version=collectedAt)
    validateChunk(chunk)
    chunks.append(chunk)
  return chunks
