from dataclasses import replace

import pytest

from labor_rag.auth import AuthorizationError, Principal
from labor_rag.policy import canRead, requireManage
from test_enterprise_corpus import enterpriseChunk
from test_pipeline import makeChunk


@pytest.mark.parametrize("role", ["employee", "manager", "hr"])
def testAuthenticatedEnterpriseRolesCanReadPublicOfficialSources(role):
  for sourceType in ("官方摘要", "官方問答", "裁罰紀錄"):
    assert canRead(Principal("personal", role), replace(makeChunk(), sourceType=sourceType))


@pytest.mark.parametrize("role,allowedRoles,expected", [
  ("employee", ("employee", "manager", "hr"), True),
  ("employee", ("manager", "hr"), False),
  ("employee", ("hr",), False),
  ("manager", ("employee",), False),
  ("manager", ("manager", "hr"), True),
  ("hr", ("hr",), True),
  ("hr", ("employee",), False)])
def testInternalAclGrantsOnlyExplicitRoles(role, allowedRoles, expected):
  assert canRead(Principal("personal", role), enterpriseChunk(allowedRoles=allowedRoles)) is expected


@pytest.mark.parametrize("allowedRoles", [(), None, "employee", ("public",),
  ("employee", "public"), ("employee", "unknown")])
def testMissingMalformedOrPublicInternalAclFailsClosed(allowedRoles):
  assert not canRead(Principal("personal", "employee"), enterpriseChunk(allowedRoles=allowedRoles))


@pytest.mark.parametrize("changes", [{"active": False}, {"active": 1},
  {"publicationStatus": "draft"}, {"publicationStatus": "withdrawn"},
  {"publicationStatus": "unknown"}, {"sourceType": "unknown"},
  {"sourceUrl": "https://example.com/demo"}, {"maintainerId": None}])
def testInactiveUnpublishedAndMalformedInternalSourcesFailClosed(changes):
  assert not canRead(Principal("personal", "hr"), enterpriseChunk(**changes))


@pytest.mark.parametrize("allowedRoles,expected", [(("employee",), True),
  (("hr",), False), ((), False), (("public", "unknown"), False)])
def testOfficialSourcesStillRequireExplicitValidAcl(allowedRoles, expected):
  assert canRead(Principal("personal", "employee"), replace(makeChunk(), allowedRoles=allowedRoles)) is expected


def testOfficialSourceCannotDisguiseAnInternalUrlAsPublic():
  assert not canRead(Principal("personal", "employee"), replace(makeChunk(), sourceUrl="internal://demo-hr"))


@pytest.mark.parametrize("principal", [None, Principal("personal", "public"), Principal("personal", "admin")])
def testInvalidPrincipalCannotReadOrManage(principal):
  assert not canRead(principal, makeChunk())
  with pytest.raises(AuthorizationError):
    requireManage(principal)


@pytest.mark.parametrize("role,capabilities", [("employee", ()), ("manager", ()),
  ("hr", ()), ("employee", ("knowledge:manage",)), ("manager", ("knowledge:manage",))])
def testManagementRequiresBothHrRoleAndCapability(role, capabilities):
  with pytest.raises(AuthorizationError):
    requireManage(Principal("personal", role, capabilities))


def testHrWithManagementCapabilityCanManage():
  assert requireManage(Principal("personal", "hr", ("knowledge:manage",))) is None
