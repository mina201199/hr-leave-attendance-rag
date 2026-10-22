from labor_rag.enterprise_display import conciseExcerpt


def testRelevantSentenceKeepsItsConditionAndDropsOtherSteps():
  text = "請假前請準備交接清單。找不到代理人時，先向主管反映，由主管協調安排。完成後更新交接內容。"
  answer = conciseExcerpt(text, "找不到代理人怎麼辦？")
  assert "找不到代理人時，先向主管反映，由主管協調安排。" in answer
  assert "完成後更新交接內容" not in answer


def testFlowKeepsFollowingExceptionWithoutTruncatingIt():
  text = "員工先填寫起訖與交接代理安排，再送主管確認。代理安排尚未確認時先補齊說明再送出。完成後留存表單。"
  answer = conciseExcerpt(text, "請假流程")
  assert answer.startswith("你可以先填寫")
  assert "代理安排尚未確認時先補齊說明再送出。" in answer
  assert "完成後留存表單" not in answer


def testSemicolonExceptionAndBracketContentRemainIntact():
  text = "先通知主管；無法聯絡時，通知代理窗口，註明[緊急]。之後再補表單。"
  assert conciseExcerpt(text, "通知誰？") == "先通知主管；無法聯絡時，通知代理窗口，註明[緊急]。"


def testPrecedingScopeIsNotDroppedWhenSelectingLaterSentence():
  text = "僅限已確認的設備故障適用。補打卡需提供發生時間與說明。"
  assert conciseExcerpt(text, "補打卡需提供什麼？") == text
