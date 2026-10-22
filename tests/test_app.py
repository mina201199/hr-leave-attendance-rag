from pathlib import Path

from streamlit.testing.v1 import AppTest
import pytest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture(autouse=True)
def isolateRuntime(tmp_path, monkeypatch):
  monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
  monkeypatch.setenv("RAG_PROVIDER", "extractive")
  monkeypatch.setenv("RAG_RETRIEVAL", "bm25")
  monkeypatch.setenv("RAG_RERANKER_PATH", "")


def testQuestionShowsAnswerAndOfficialCitation():
  app = AppTest.from_file(str(APP_PATH)).run()
  assert not app.exception
  app.text_input(key="question").input("打破杯子老闆可以扣薪嗎？")
  app.button(key="searchSubmit").click().run()
  assert not app.exception
  assert any("扣" in item.value for item in app.markdown)
  assert any("8D48F35D4240FBEC" in item.value for item in app.markdown)


def testUnknownQuestionShowsNoEvidence():
  app = AppTest.from_file(str(APP_PATH)).run()
  app.text_input(key="question").input("火星火山地質")
  app.button(key="searchSubmit").click().run()
  assert not app.exception
  assert any("找不到" in item.value for item in app.warning)


def testFeedbackSurvivesRerunAndTraceIsVisible():
  app = AppTest.from_file(str(APP_PATH)).run()
  app.text_input(key="question").input("打破杯子老闆可以扣薪嗎？")
  app.button(key="searchSubmit").click().run()
  app.button(key="feedbackHelpful").click().run()
  assert not app.exception
  assert any("回饋已儲存" in item.value for item in app.success)

