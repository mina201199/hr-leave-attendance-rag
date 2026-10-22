from dataclasses import replace

from .models import SearchHit


def buildContext(hits: list[SearchHit], maxChars: int = 8000) -> list[SearchHit]:
  result, seen, seenText, used = [], set(), set(), 0
  for hit in hits:
    chunk = hit.chunk
    text = chunk.parentText or chunk.text
    identity = (chunk.documentId, chunk.version, chunk.parentId or chunk.chunkId)
    if identity in seen or text in seenText or used + len(text) > maxChars:
      continue
    seen.add(identity)
    seenText.add(text)
    used += len(text)
    result.append(SearchHit(replace(chunk, text=text), hit.score))
  return result
