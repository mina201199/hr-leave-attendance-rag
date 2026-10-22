import json
from datetime import datetime, timezone

from .storage import KnowledgeStore


class TraceStore(KnowledgeStore):
  def __init__(self, path):
    super().__init__(path)
    with self.connect() as connection:
      connection.execute("CREATE TABLE IF NOT EXISTS traces (traceId TEXT PRIMARY KEY, createdAt TEXT NOT NULL, payload TEXT NOT NULL, feedback TEXT)")

  def record(self, traceId, payload):
    with self.connect() as connection:
      connection.execute("INSERT INTO traces VALUES (?, ?, ?, NULL)", (traceId, datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)))

  def feedback(self, traceId, rating):
    if rating not in ("helpful", "unhelpful"):
      raise ValueError("不支援的回饋")
    with self.connect() as connection:
      cursor = connection.execute("UPDATE traces SET feedback=? WHERE traceId=?", (rating, traceId))
      if cursor.rowcount != 1:
        raise ValueError("找不到查詢紀錄")

  def recent(self, limit=50):
    with self.connect() as connection:
      rows = connection.execute("SELECT traceId, createdAt, payload, feedback FROM traces ORDER BY createdAt DESC LIMIT ?", (limit,)).fetchall()
    return [{"traceId": traceId, "createdAt": createdAt, **json.loads(payload), "feedback": feedback} for traceId, createdAt, payload, feedback in rows]
