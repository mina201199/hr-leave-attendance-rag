from pathlib import Path
from uuid import uuid4


def pytest_configure(config):
  if config.option.basetemp is None:
    workspacePath = Path(__file__).resolve().parents[1]
    temporaryPath = (workspacePath / ".test-temp" / uuid4().hex).resolve()
    if not temporaryPath.is_relative_to(workspacePath) or temporaryPath.exists():
      raise RuntimeError("測試暫存目錄不安全")
    temporaryPath.parent.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(temporaryPath)

