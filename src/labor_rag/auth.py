import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROLES = ("employee", "manager", "hr")


class AuthenticationError(Exception):
  def __init__(self):
    super().__init__("Authentication failed")


class AuthorizationError(Exception):
  def __init__(self):
    super().__init__("Authorization denied")


@dataclass(frozen=True)
class Principal:
  userId: str
  role: str
  capabilities: tuple[str, ...] = ()

  def __post_init__(self):
    object.__setattr__(self, "capabilities", tuple(self.capabilities))


class AuthStore:
  def __init__(self, path: str | Path, clock: Callable[[], float] = time.time):
    self.path = Path(path)
    self.clock = clock
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self.dummySalt = secrets.token_bytes(16)
    self.dummyHash = self._hashPassword(secrets.token_urlsafe(32), self.dummySalt)
    with self.connect() as connection:
      connection.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
          userId TEXT PRIMARY KEY, passwordSalt BLOB NOT NULL, passwordHash BLOB NOT NULL,
          role TEXT NOT NULL, capabilities TEXT NOT NULL, enabled INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
          tokenHash TEXT PRIMARY KEY, userId TEXT NOT NULL, expiresAt REAL NOT NULL,
          FOREIGN KEY (userId) REFERENCES accounts(userId)
        );
        CREATE TABLE IF NOT EXISTS loginAttempts (
          userKey TEXT PRIMARY KEY, failedCount INTEGER NOT NULL, windowStart REAL NOT NULL
        );
      """)

  @contextmanager
  def connect(self):
    connection = sqlite3.connect(self.path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
      with connection:
        yield connection
    finally:
      connection.close()

  @staticmethod
  def _validateUserId(userId):
    if not isinstance(userId, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}", userId) is None:
      raise ValueError("Invalid account details")

  @staticmethod
  def _validatePassword(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
      raise ValueError("Password must contain 12 to 256 characters")
    try:
      password.encode("utf-8")
    except UnicodeError:
      raise ValueError("Invalid password encoding") from None

  @staticmethod
  def _validateRole(role, capabilities):
    if role not in ROLES or not isinstance(capabilities, (tuple, list)):
      raise ValueError("Invalid account permissions")
    if any(capability != "knowledge:manage" for capability in capabilities) or (capabilities and role != "hr"):
      raise ValueError("Invalid account permissions")
    return tuple(sorted(set(capabilities)))

  @staticmethod
  def _hashPassword(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)

  def createUser(self, userId: str, password: str, role: str, capabilities: tuple[str, ...] = ()) -> None:
    self._validateUserId(userId)
    self._validatePassword(password)
    capabilities = self._validateRole(role, capabilities)
    salt = secrets.token_bytes(16)
    passwordHash = self._hashPassword(password, salt)
    try:
      with self.connect() as connection:
        connection.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, 1)",
          (userId, salt, passwordHash, role, json.dumps(capabilities)))
    except sqlite3.IntegrityError:
      raise ValueError("Invalid account details") from None

  def login(self, userId: str, password: str) -> str:
    validInput = True
    try:
      self._validateUserId(userId)
      self._validatePassword(password)
    except ValueError:
      validInput = False
    userKey = hashlib.sha256((userId if isinstance(userId, str) else "").encode("utf-8", errors="replace")).hexdigest()
    token = None
    with self.connect() as connection:
      connection.execute("BEGIN IMMEDIATE")
      now = self.clock()
      connection.execute("DELETE FROM loginAttempts WHERE windowStart<=?", (now - 5 * 60,))
      attempts = connection.execute("SELECT failedCount FROM loginAttempts WHERE userKey=?", (userKey,)).fetchone()
      account = connection.execute("SELECT * FROM accounts WHERE userId=?", (userId,)).fetchone() if validInput else None
      salt = account["passwordSalt"] if account else self.dummySalt
      expectedHash = account["passwordHash"] if account else self.dummyHash
      candidateHash = self._hashPassword(password if validInput else "", salt)
      if not hmac.compare_digest(candidateHash, expectedHash) or account is None or account["enabled"] != 1 or (attempts and attempts["failedCount"] >= 5):
        connection.execute("INSERT INTO loginAttempts VALUES (?, 1, ?) ON CONFLICT(userKey) DO UPDATE SET failedCount=failedCount+1", (userKey, now))
      else:
        connection.execute("DELETE FROM loginAttempts WHERE userKey=?", (userKey,))
        token = secrets.token_urlsafe(32)
        tokenHash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (tokenHash, userId, now + 8 * 60 * 60))
    # 失敗紀錄必須先提交，不能因拋出驗證錯誤而整筆回復。
    if token is None:
      raise AuthenticationError()
    return token

  def authenticate(self, token: str) -> Principal:
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
      raise AuthenticationError()
    tokenHash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with self.connect() as connection:
      account = connection.execute("SELECT a.* FROM sessions s JOIN accounts a ON a.userId=s.userId WHERE s.tokenHash=? AND s.expiresAt>? AND a.enabled=1",
        (tokenHash, self.clock())).fetchone()
    if account is None:
      raise AuthenticationError()
    try:
      capabilities = self._validateRole(account["role"], json.loads(account["capabilities"]))
    except (ValueError, TypeError):
      raise AuthenticationError() from None
    return Principal(account["userId"], account["role"], capabilities)

  def logout(self, token: str) -> None:
    if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
      return
    tokenHash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with self.connect() as connection:
      connection.execute("DELETE FROM sessions WHERE tokenHash=?", (tokenHash,))

  def _updateAccount(self, userId, assignment, values):
    self._validateUserId(userId)
    with self.connect() as connection:
      cursor = connection.execute("UPDATE accounts SET " + assignment + " WHERE userId=?", (*values, userId))
      if cursor.rowcount != 1:
        raise AuthenticationError()
      connection.execute("DELETE FROM sessions WHERE userId=?", (userId,))

  def setRole(self, userId: str, role: str, capabilities: tuple[str, ...] = ()) -> None:
    capabilities = self._validateRole(role, capabilities)
    self._updateAccount(userId, "role=?, capabilities=?", (role, json.dumps(capabilities)))

  def setEnabled(self, userId: str, enabled: bool) -> None:
    if not isinstance(enabled, bool):
      raise ValueError("Enabled must be a boolean")
    self._updateAccount(userId, "enabled=?", (int(enabled),))

  def setPassword(self, userId: str, password: str) -> None:
    self._validatePassword(password)
    salt = secrets.token_bytes(16)
    self._updateAccount(userId, "passwordSalt=?, passwordHash=?", (salt, self._hashPassword(password, salt)))
