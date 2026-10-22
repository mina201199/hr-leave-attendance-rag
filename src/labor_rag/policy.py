from .auth import AuthorizationError, Principal, ROLES
from .ingestion import ENTERPRISE_SOURCE_TYPES, validateChunk
from .models import DocumentChunk


def canRead(principal: Principal, chunk: DocumentChunk) -> bool:
  if not isinstance(principal, Principal) or principal.role not in ROLES or not isinstance(chunk, DocumentChunk):
    return False
  if chunk.active is not True or chunk.publicationStatus != "published":
    return False
  try:
    # 服務可能收到尚未經儲存層驗證的片段，因此政策本身也必須拒絕錯誤 ACL 與來源。
    validateChunk(chunk)
  except (ValueError, TypeError, AttributeError):
    return False
  if any(role not in (*ROLES, "public") for role in chunk.allowedRoles):
    return False
  if chunk.sourceType in ENTERPRISE_SOURCE_TYPES:
    return "public" not in chunk.allowedRoles and principal.role in chunk.allowedRoles
  return "public" in chunk.allowedRoles or principal.role in chunk.allowedRoles


def requireManage(principal: Principal) -> None:
  if not isinstance(principal, Principal) or principal.role != "hr" or "knowledge:manage" not in principal.capabilities:
    raise AuthorizationError()
