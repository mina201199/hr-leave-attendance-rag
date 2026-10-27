import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .retrieval import tokenize


def currentPolicyDate():
  return datetime.now(ZoneInfo("Asia/Taipei")).date()


def conciseExcerpt(text, question):
  """以本機比對挑選完整句子，不生成新制度或硬切字數。"""
  sentences = re.findall(r"[^。！？\n]+[。！？]?", text)
  if not sentences:
    return text
  questionTokens = set(tokenize(question))
  scores = [len(questionTokens & set(tokenize(sentence))) for sentence in sentences]
  bestIndex = max(range(len(sentences)), key=lambda index: scores[index])
  start = bestIndex
  # 指代或延續句不能脫離前文；分號內的條件永遠與原句保留。
  if start and re.match(r"^(其|此|上述|該|再|並|完成後)", sentences[start]):
    start -= 1
  for index in range(start):
    if re.search(r"僅限|只限|適用|前提|條件|必須|不得|若|除非", sentences[index]):
      start = index
      break
  end = bestIndex + 1
  while end < len(sentences) and re.search(r"^(若|如|但|除|否則)|^[^。]{0,35}(時|尚未|才能|不得|必須)", sentences[end]):
    end += 1
  excerpt = "".join(sentences[start:end])
  return re.sub(r"^員工先", "你可以先", excerpt)
