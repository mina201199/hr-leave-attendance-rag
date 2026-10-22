from typing import Protocol

from .models import GeneratedAnswer, SearchHit


class AnswerGenerator(Protocol):
  mode: str

  def generate(self, question: str, hits: list[SearchHit]) -> GeneratedAnswer:
    ...


class ExtractiveGenerator:
  mode = "來源摘錄"

  def generate(self, question: str, hits: list[SearchHit]) -> GeneratedAnswer:
    bestChunk = hits[0].chunk
    return GeneratedAnswer(text=bestChunk.text, citationIds=[bestChunk.chunkId])

