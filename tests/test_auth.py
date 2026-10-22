import hashlib
import importlib.util
import secrets
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from labor_rag.auth import AuthenticationError, AuthStore, Principal


def randomPassword(length=32):
  return secrets.token_urlsafe(256)[:length]


def testLoginPersistsSaltedPasswordAndOnlyHashedSession(tmp_path):
  path = tmp_path / "auth.db"
  store = AuthStore(path)
  password = randomPassword()
  store.createUser("personal.hr", password, "hr", ("knowledge:manage",))
  store.createUser("personal.other", password, "employee")
  token = store.login("personal.hr", password)
  principal = AuthStore(path).authenticate(token)
  assert principal == Principal("personal.hr", "hr", ("knowledge:manage",))
  with pytest.raises(FrozenInstanceError):
    principal.role = "employee"
  with sqlite3.connect(path) as connection:
    rows = connection.execute("SELECT passwordSalt, passwordHash FROM accounts").fetchall()
    storedToken = connection.execute("SELECT tokenHash FROM sessions").fetchone()[0]
  assert rows[0] != rows[1]
  assert all(len(salt) >= 16 and len(passwordHash) >= 32 for salt, passwordHash in rows)
  assert storedToken == hashlib.sha256(token.encode()).hexdigest()
  rawData = path.read_bytes()
  assert password.encode() not in rawData
  assert token.encode() not in rawData


def testUnknownAccountWrongPasswordAndUnknownSessionHaveSameError(tmp_path):
  store = AuthStore(tmp_path / "auth.db")
  password = randomPassword()
  store.createUser("employee", password, "employee")
  errors = []
  for operation in (lambda: store.login("unknown", password),
      lambda: store.login("employee", randomPassword()),
      lambda: store.authenticate(secrets.token_urlsafe(32)),
      lambda: store.login(None, None), lambda: store.authenticate(None)):
    with pytest.raises(AuthenticationError) as error:
      operation()
    errors.append(str(error.value))
  assert len(set(errors)) == 1


def testMalformedUnicodePasswordUsesTheSameAuthenticationError(tmp_path):
  store = AuthStore(tmp_path / "auth.db")
  store.createUser("personal", randomPassword(), "employee")
  malformedPassword = randomPassword() + chr(0xD800)
  with pytest.raises(AuthenticationError) as expectedError:
    store.login("missing", randomPassword())
  with pytest.raises(AuthenticationError) as actualError:
    store.login("personal", malformedPassword)
  assert str(actualError.value) == str(expectedError.value)


def testLogoutIgnoresMalformedTokensWithoutTouchingValidSessions(tmp_path):
  store = AuthStore(tmp_path / "auth.db")
  password = randomPassword()
  store.createUser("personal", password, "employee")
  token = store.login("personal", password)
  for invalidToken in (None, chr(0xD800), ""):
    store.logout(invalidToken)
  assert store.authenticate(token).userId == "personal"


@pytest.mark.parametrize("userId", ["", " leading", "trailing ", "has space", "a\n", "x" * 65, "../admin", None])
def testMalformedUsernamesCannotBeCreated(tmp_path, userId):
  store = AuthStore(tmp_path / "auth.db")
  with pytest.raises(ValueError):
    store.createUser(userId, randomPassword(), "employee")


@pytest.mark.parametrize("role,capabilities", [("admin", ()), ("public", ()),
  ("employee", ("knowledge:manage",)), ("manager", ("knowledge:manage",)),
  ("hr", ("unknown",)), ("hr", "knowledge:manage"), (None, ())])
def testInvalidRolesOrCapabilitiesCannotBeCreated(tmp_path, role, capabilities):
  with pytest.raises(ValueError):
    AuthStore(tmp_path / "auth.db").createUser("personal", randomPassword(), role, capabilities)


@pytest.mark.parametrize("length", [11, 257])
def testPasswordLengthLimitsRejectOutOfRangeValues(tmp_path, length):
  with pytest.raises(ValueError):
    AuthStore(tmp_path / "auth.db").createUser("personal", randomPassword(length), "employee")


@pytest.mark.parametrize("length", [12, 256])
def testPasswordLengthBoundariesCanLogin(tmp_path, length):
  store = AuthStore(tmp_path / "auth.db")
  password = randomPassword(length)
  store.createUser("personal", password, "employee")
  assert store.authenticate(store.login("personal", password)).userId == "personal"


def testDuplicateAccountCannotOverwriteExistingCredentials(tmp_path):
  store = AuthStore(tmp_path / "auth.db")
  password = randomPassword()
  store.createUser("personal", password, "employee")
  with pytest.raises(ValueError):
    store.createUser("personal", randomPassword(), "hr", ("knowledge:manage",))
  assert store.authenticate(store.login("personal", password)).role == "employee"


def testPrincipalCopiesCapabilitiesToKeepThemImmutable():
  capabilities = ["knowledge:manage"]
  principal = Principal("personal", "hr", capabilities)
  capabilities.clear()
  assert principal.capabilities == ("knowledge:manage",)


def testLogoutRevokesOnlyTheSpecifiedSession(tmp_path):
  store = AuthStore(tmp_path / "auth.db")
  password = randomPassword()
  store.createUser("personal", password, "employee")
  firstToken = store.login("personal", password)
  secondToken = store.login("personal", password)
  store.logout(firstToken)
  store.logout(firstToken)
  with pytest.raises(AuthenticationError):
    store.authenticate(firstToken)
  assert store.authenticate(secondToken).userId == "personal"


def testSessionExpiresAtEightHoursWithoutSlidingOnAuthentication(tmp_path):
  currentTime = [1_000_000.0]
  store = AuthStore(tmp_path / "auth.db", clock=lambda: currentTime[0])
  password = randomPassword()
  store.createUser("personal", password, "employee")
  token = store.login("personal", password)
  currentTime[0] += 8 * 60 * 60 - 1
  assert store.authenticate(token).role == "employee"
  currentTime[0] += 1
  with pytest.raises(AuthenticationError):
    store.authenticate(token)


@pytest.mark.parametrize("change", ["role", "capabilities", "password", "disable", "enable"])
def testAccountUpdatesRevokeAllSessionsAcrossStoreInstances(tmp_path, change):
  path = tmp_path / "auth.db"
  store = AuthStore(path)
  password = randomPassword()
  newPassword = randomPassword()
  store.createUser("personal", password, "hr")
  store.createUser("other", password, "employee")
  tokens = [store.login("personal", password) for _ in range(2)]
  otherToken = store.login("other", password)
  adminStore = AuthStore(path)
  if change == "role":
    adminStore.setRole("personal", "manager")
  elif change == "capabilities":
    adminStore.setRole("personal", "hr", ("knowledge:manage",))
  elif change == "password":
    adminStore.setPassword("personal", newPassword)
  else:
    adminStore.setEnabled("personal", change == "enable")
  for token in tokens:
    with pytest.raises(AuthenticationError):
      store.authenticate(token)
  assert store.authenticate(otherToken).userId == "other"
  if change == "disable":
    with pytest.raises(AuthenticationError):
      store.login("personal", password)
    adminStore.setEnabled("personal", True)
    for token in tokens:
      with pytest.raises(AuthenticationError):
        store.authenticate(token)
  else:
    principal = store.authenticate(store.login("personal", newPassword if change == "password" else password))
    assert principal.role == ("manager" if change == "role" else "hr")
    assert principal.capabilities == (("knowledge:manage",) if change == "capabilities" else ())
  if change == "password":
    with pytest.raises(AuthenticationError):
      store.login("personal", password)


def testAuthenticationRereadsAccountStatusAndPermissions(tmp_path):
  path = tmp_path / "auth.db"
  store = AuthStore(path)
  password = randomPassword()
  store.createUser("personal", password, "hr", ("knowledge:manage",))
  token = store.login("personal", password)
  with sqlite3.connect(path) as connection:
    connection.execute("UPDATE accounts SET role='employee', capabilities='[]'")
  assert store.authenticate(token) == Principal("personal", "employee", ())
  with sqlite3.connect(path) as connection:
    connection.execute("UPDATE accounts SET enabled=0")
  with pytest.raises(AuthenticationError):
    store.authenticate(token)


def testInvalidAdministrativeChangesPreserveExistingAccountAndSession(tmp_path):
  store = AuthStore(tmp_path / "auth.db")
  password = randomPassword()
  store.createUser("personal", password, "employee")
  token = store.login("personal", password)
  for operation in (lambda: store.setRole("personal", "employee", ("knowledge:manage",)),
      lambda: store.setRole("personal", "unknown"),
      lambda: store.setPassword("personal", randomPassword(11)),
      lambda: store.setEnabled("personal", "false")):
    with pytest.raises(ValueError):
      operation()
  assert store.authenticate(token).role == "employee"
  for operation in (lambda: store.setRole("missing", "employee"),
      lambda: store.setPassword("missing", randomPassword()),
      lambda: store.setEnabled("missing", False)):
    with pytest.raises(AuthenticationError):
      operation()


@pytest.mark.parametrize("knownAccount", [False, True])
def testRepeatedFailedLoginIsThrottledWithoutExistenceLeakage(tmp_path, knownAccount):
  currentTime = [1_000_000.0]
  path = tmp_path / "auth.db"
  store = AuthStore(path, clock=lambda: currentTime[0])
  password = randomPassword()
  if knownAccount:
    store.createUser("personal", password, "employee")
  errors = []
  for _ in range(5):
    with pytest.raises(AuthenticationError) as error:
      store.login("personal", randomPassword())
    errors.append(str(error.value))
  if not knownAccount:
    store.createUser("personal", password, "employee")
  reopened = AuthStore(path, clock=lambda: currentTime[0])
  with pytest.raises(AuthenticationError) as error:
    reopened.login("personal", password)
  errors.append(str(error.value))
  assert len(set(errors)) == 1
  currentTime[0] += 5 * 60
  assert reopened.authenticate(reopened.login("personal", password)).userId == "personal"


def loadAccountScript(tmp_path, monkeypatch):
  scriptPath = Path(__file__).resolve().parents[1] / "scripts/manage_accounts.py"
  spec = importlib.util.spec_from_file_location("manageAccountScript", scriptPath)
  script = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(script)
  monkeypatch.setattr(script, "projectPath", tmp_path)
  monkeypatch.delenv("RAG_ENTERPRISE_DATA_DIR", raising=False)
  return script


def testAccountCliCreatesThreePersonalAccountsUsingHiddenPrompts(tmp_path, monkeypatch, capsys):
  script = loadAccountScript(tmp_path, monkeypatch)
  dataDir = tmp_path / "isolated"
  for role in ("employee", "manager", "hr"):
    password = randomPassword()
    prompts = iter([password, password])
    monkeypatch.setattr(script, "getpass", lambda prompt: next(prompts))
    args = ["create", "--user", "personal." + role, "--role", role, "--data-dir", str(dataDir)]
    script.main(args + (["--manage"] if role == "hr" else []))
    principal = AuthStore(dataDir / "auth.db").authenticate(AuthStore(dataDir / "auth.db").login("personal." + role, password))
    assert principal.role == role
    assert principal.capabilities == (("knowledge:manage",) if role == "hr" else ())
    output = capsys.readouterr()
    assert password not in output.out + output.err
  assert not (tmp_path / "data").exists()


def testAccountCliChangesRolePasswordAndEnabledState(tmp_path, monkeypatch):
  script = loadAccountScript(tmp_path, monkeypatch)
  dataDir = tmp_path / "isolated"
  store = AuthStore(dataDir / "auth.db")
  password = randomPassword()
  store.createUser("personal", password, "employee")
  token = store.login("personal", password)
  sharedArgs = ["--user", "personal", "--data-dir", str(dataDir)]
  script.main(["set-role", *sharedArgs, "--role", "hr", "--manage"])
  with pytest.raises(AuthenticationError):
    store.authenticate(token)
  assert store.authenticate(store.login("personal", password)).capabilities == ("knowledge:manage",)
  newPassword = randomPassword()
  prompts = iter([newPassword, newPassword])
  monkeypatch.setattr(script, "getpass", lambda prompt: next(prompts))
  script.main(["set-password", *sharedArgs])
  with pytest.raises(AuthenticationError):
    store.login("personal", password)
  script.main(["disable", *sharedArgs])
  with pytest.raises(AuthenticationError):
    store.login("personal", newPassword)
  script.main(["enable", *sharedArgs])
  assert store.authenticate(store.login("personal", newPassword)).role == "hr"


@pytest.mark.parametrize("pathSource", ["default", "dotenv", "environment", "argument"])
def testAccountCliResolvesEnterpriseDataDirectoryWithoutReadingRealEnvironment(tmp_path, monkeypatch, pathSource):
  script = loadAccountScript(tmp_path, monkeypatch)
  if pathSource != "default":
    (tmp_path / ".env").write_text("RAG_ENTERPRISE_DATA_DIR=dotenv-store\n", encoding="utf-8")
  if pathSource in ("environment", "argument"):
    monkeypatch.setenv("RAG_ENTERPRISE_DATA_DIR", "environment-store")
  args = ["create", "--user", "personal", "--role", "employee"]
  if pathSource == "argument":
    args += ["--data-dir", str(tmp_path / "argument-store")]
  password = randomPassword()
  monkeypatch.setattr(script, "getpass", lambda prompt: password)
  script.main(args)
  directory = {"default": "data/index/enterprise", "dotenv": "dotenv-store",
    "environment": "environment-store", "argument": "argument-store"}[pathSource]
  assert (tmp_path / directory / "auth.db").exists()


def testAccountCliRejectsPasswordArgumentsAndMismatchBeforeCreatingDatabase(tmp_path, monkeypatch, capsys):
  script = loadAccountScript(tmp_path, monkeypatch)
  args = ["create", "--user", "personal", "--role", "employee"]
  password = randomPassword()
  with pytest.raises(SystemExit) as error:
    script.main([*args, "--password", password])
  assert error.value.code == 2
  output = capsys.readouterr()
  assert password not in output.out + output.err
  prompts = iter([password, randomPassword()])
  monkeypatch.setattr(script, "getpass", lambda prompt: next(prompts))
  with pytest.raises(SystemExit) as error:
    script.main(args)
  assert error.value.code == 1
  assert not (tmp_path / "data").exists()
  capsys.readouterr()
