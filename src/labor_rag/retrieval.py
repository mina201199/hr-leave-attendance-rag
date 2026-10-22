import math
import re
from collections import Counter

from .models import DocumentChunk, SearchHit


def tokenize(text: str) -> list[str]:
  text = text.lower()
  for original, normalized in (("老闆", "雇主"), ("薪水", "工資"), ("薪資", "工資"), ("扣薪", "扣發工資"), ("特休", "特別休假")):
    text = text.replace(original, normalized)
  tokens = re.findall(r"[a-z0-9]+", text)
  for segment in re.findall(r"[\u4e00-\u9fff]+", text):
    tokens.extend(segment[index:index + 2] for index in range(len(segment) - 1))
  stopTokens = {"可以", "是否", "什麼", "如何", "怎麼", "哪些", "有沒有", "相關", "資料"}
  return [token for token in tokens if token not in stopTokens]


class Retriever:
  def __init__(self, chunks: list[DocumentChunk]):
    if len({chunk.chunkId for chunk in chunks}) != len(chunks):
      raise ValueError("索引包含重複的 chunkId")
    self.chunks = list(chunks)
    self.termCounts = [Counter(tokenize(chunk.title + " " + chunk.text)) for chunk in chunks]
    self.documentFrequency = Counter(token for counts in self.termCounts for token in counts)
    self.documentLengths = [sum(counts.values()) for counts in self.termCounts]
    self.averageLength = sum(self.documentLengths) / max(len(chunks), 1) or 1

  def search(self, question: str, topK: int = 3, category: str | None = None, companyName: str | None = None) -> list[SearchHit]:
    if not 1 <= topK <= 10:
      raise ValueError("topK 必須介於 1 與 10")
    questionTokens = set(tokenize(question))
    hits = []
    for index, chunk in enumerate(self.chunks):
      if category and chunk.category != category:
        continue
      if companyName and chunk.companyName != companyName.strip():
        continue
      score = 0.0
      for token in questionTokens:
        frequency = self.termCounts[index].get(token, 0)
        if not frequency:
          continue
        inverseFrequency = math.log(1 + (len(self.chunks) - self.documentFrequency[token] + 0.5) / (self.documentFrequency[token] + 0.5))
        lengthFactor = 1.2 * (0.25 + 0.75 * self.documentLengths[index] / self.averageLength)
        score += inverseFrequency * frequency * 2.2 / (frequency + lengthFactor)
      if score > 0 or companyName:
        hits.append(SearchHit(chunk=chunk, score=score))
    return sorted(hits, key=lambda hit: (-hit.score, hit.chunk.chunkId))[:topK]

