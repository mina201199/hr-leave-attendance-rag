from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocumentChunk:
  chunkId: str
  documentId: str
  title: str
  text: str
  sourceUrl: str
  publisher: str
  category: str
  sourceType: str
  collectedAt: str
  sourceUpdatedAt: str | None = None
  companyName: str | None = None
  version: str = "1"
  active: bool = True
  allowedRoles: tuple[str, ...] = ("public",)
  effectiveFrom: str | None = None
  effectiveTo: str | None = None
  parentId: str | None = None
  parentText: str | None = None


@dataclass(frozen=True)
class SearchHit:
  chunk: DocumentChunk
  score: float


@dataclass(frozen=True)
class GeneratedAnswer:
  text: str
  citationIds: list[str]
  answerable: bool = True
  usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerResponse:
  status: str
  text: str
  citations: list[DocumentChunk]
  mode: str
  hits: list[SearchHit] = field(default_factory=list)
  traceId: str | None = None
  elapsedMs: float = 0

