import re
from dataclasses import replace

from .models import DocumentChunk


def splitChunk(chunk: DocumentChunk, maxChars: int = 500, overlapChars: int = 60) -> list[DocumentChunk]:
  if maxChars < 1 or not 0 <= overlapChars < maxChars:
    raise ValueError("分段長度與重疊長度設定無效")
  text = chunk.text.replace("\r\n", "\n").strip()
  if len(text) <= maxChars:
    return [replace(chunk, text=text)]
  sentences = re.findall(r"[^。！？\n]+[。！？]?|\n+", text)
  groups, current = [], []
  for sentence in sentences:
    if current and len("".join(current)) + len(sentence) > maxChars:
      groups.append("".join(current).strip())
      overlap = []
      for previous in reversed(current):
        if len("".join(overlap)) + len(previous) > overlapChars:
          break
        overlap.insert(0, previous)
      current = overlap
    current.append(sentence)
  if current:
    groups.append("".join(current).strip())
  return [replace(chunk, chunkId=f"{chunk.chunkId}--{index + 1}", text=part, parentId=chunk.chunkId, parentText=text) for index, part in enumerate(groups) if part]
