"""Local accounts; only hashed passwords and hashed opaque login tokens are stored."""
from .errors import UserError
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import sqlite3
from .store import now, uid

COOKIE = "athena_login"


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${value}"


def verify(password, encoded):
    _, _, salt, expected = encoded.split("$")
    return hmac.compare_digest(password_hash(password, salt).rsplit("$", 1)[1], expected)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, store):
        self.store = store

    async def register(self, username, password):
        encoded = await asyncio.to_thread(password_hash, password)
        def insert(db):
            first = not db.execute("SELECT 1 FROM users").fetchone()
            user = {"id": uid(), "username": username.strip(), "role": "owner" if first else "user", "developer_mode": False}
            try:
                db.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES (?,?,?,?,?)", (user["id"], user["username"], encoded, user["role"], now()))
            except sqlite3.IntegrityError:
                raise UserError("username_taken", "这个用户名已经被使用了")
            if first:
                project = "personal:" + user["id"]
                db.execute("INSERT OR IGNORE INTO projects VALUES (?, '我的事情')", (project,))
                db.execute("UPDATE sessions SET user_id=?,project_id=? WHERE user_id IS NULL", (user["id"], project))
                db.execute("UPDATE memories SET owner_id=? WHERE owner_id='global'", ("global:" + user["id"],))
                db.execute("UPDATE memories SET owner_id=? WHERE owner_id='personal'", (project,))
            return user
        user = await self.store.call(insert)
        return user, await self.issue(user["id"])

    async def login(self, username, password):
        username = username.strip()
        attempt_key = username.casefold()
        stamp = datetime.now(timezone.utc)
        def reserve(db):
            row = db.execute("SELECT * FROM auth_attempts WHERE username=?", (attempt_key,)).fetchone()
            if row and (stamp-datetime.fromisoformat(row["last_at"])).total_seconds() < 900 and row["attempts"] >= 10:
                raise UserError("login_rate_limited", "尝试次数较多，请 15 分钟后再试")
            attempts = row["attempts"] + 1 if row and (stamp-datetime.fromisoformat(row["last_at"])).total_seconds() < 900 else 1
            db.execute("INSERT INTO auth_attempts VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,last_at=excluded.last_at", (attempt_key, attempts, stamp.isoformat()))
            found = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(found) if found else None
        user = await self.store.call(reserve)
        # Same KDF cost for unknown accounts.
        encoded = user["password_hash"] if user else "pbkdf2_sha256$600000$" + "00"*16 + "$" + "00"*32
        valid = await asyncio.to_thread(verify, password, encoded)
        if not user or not valid:
            raise UserError("invalid_credentials", "用户名或密码不正确")
        await self.store.execute("DELETE FROM auth_attempts WHERE username=?", (attempt_key,))
        user.pop("password_hash")
        return user, await self.issue(user["id"])

    async def issue(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await self.store.execute("INSERT INTO auth_sessions VALUES (?,?,?)", (token_hash(token), user_id, expires))
        return token

    async def current(self, token):
        if not token or len(token) > 256:
            return None
        rows = await self.store.rows("SELECT u.id,u.username,u.role,u.developer_mode FROM users u JOIN auth_sessions a ON a.user_id=u.id WHERE a.token_hash=? AND a.expires_at>?", (token_hash(token), now()))
        return rows[0] if rows else None
