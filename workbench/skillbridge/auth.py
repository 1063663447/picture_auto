"""账号体系：账号存在飞书多维表格里，工作台登录时校验。

设计：
* 账号表（默认表名「账号」）字段：账号 / 显示名 / 密码哈希 / 明文密码 / 角色 / 状态 / 额度元 / 备注
* 密码优先用 `密码哈希`（`pbkdf2_sha256$迭代$盐$哈希`）；运营图省事可以直接往 `明文密码` 里写密码，
  登录成功后会自动升级为哈希并清空明文（需要应用有编辑权限）。
* 会话：`wb_session` Cookie = base64(json) + "." + HMAC-SHA256 签名（密钥取 config.server.session_secret）。
* 飞书不可用 / 未配置时，退回 `data/accounts.json`（首次启动自动生成一个 admin/inner 账号），
  仍可用 `server.auth_token` 作为机器访问口令，避免把自己锁在外面。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from .feishu import FeishuBitable, FeishuConfig

CACHE_TTL = 30.0
ITERATIONS = 260000
SESSION_TTL = 60 * 60 * 12  # 12 小时


# ------------------------------------------------------------------- 密码 --
def hash_password(password: str, salt: str | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if not stored.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(password, stored)  # 兼容直接写明文
    try:
        _, iterations, salt, expected = stored.split("$", 3)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


@dataclass
class Account:
    username: str
    display: str = ""
    role: str = "member"
    status: str = "enabled"
    quota: float = 0.0
    note: str = ""
    password_hash: str = ""
    plain: str = ""
    record_id: str = ""

    @property
    def active(self) -> bool:
        return str(self.status).strip().lower() in {"", "enabled", "启用", "是", "true"}

    @property
    def is_admin(self) -> bool:
        return str(self.role).strip().lower() == "admin"

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "display": self.display or self.username,
            "role": self.role,
            "active": self.active,
            "quota": self.quota,
        }


def _flat(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("link") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


# ------------------------------------------------------------------- 存储 --
class AccountStore:
    def __init__(self, cfg: FeishuConfig | None, data_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.data_dir = Path(data_dir or "data")
        self.accounts: dict[str, Account] = {}
        self.loaded_at = 0.0
        self.source = ""

    # -- 飞书账号表里的 table_id --
    @property
    def feishu_ready(self) -> bool:
        return bool(
            self.cfg
            and self.cfg.enabled
            and self.cfg.ready()[0]
            and getattr(self.cfg, "account_table_id", "")
        )

    def load(self, force: bool = False) -> dict[str, Account]:
        if not force and self.accounts and time.time() - self.loaded_at < CACHE_TTL:
            return self.accounts
        if self.feishu_ready:
            try:
                self.accounts = self._load_feishu()
                self.source = "feishu"
                self.loaded_at = time.time()
                return self.accounts
            except Exception:  # noqa: BLE001 - 飞书挂了就用本地兜底
                pass
        self.accounts = self._load_file()
        self.source = "local"
        self.loaded_at = time.time()
        return self.accounts

    def _load_feishu(self) -> dict[str, Account]:
        table_id = getattr(self.cfg, "account_table_id", "")
        client = FeishuBitable(self.cfg)
        items = client.search_table(table_id)

        accounts: dict[str, Account] = {}
        for item in items:
            fields = item.get("fields") or {}
            username = _flat(fields.get("账号")).strip()
            if not username:
                continue
            accounts[username.lower()] = Account(
                username=username,
                display=_flat(fields.get("显示名")).strip(),
                role=_flat(fields.get("角色")).strip() or "member",
                status=_flat(fields.get("状态")).strip() or "enabled",
                quota=float(fields.get("额度元") or 0),
                note=_flat(fields.get("备注")).strip(),
                password_hash=_flat(fields.get("密码哈希")).strip(),
                plain=_flat(fields.get("明文密码")).strip(),
                record_id=str(item.get("record_id") or ""),
            )
        return accounts

    def _file_path(self) -> Path:
        return self.data_dir / "accounts.json"

    def _load_file(self) -> dict[str, Account]:
        path = self._file_path()
        if not path.is_file():
            bootstrap = Account(
                username="admin",
                display="管理员",
                role="admin",
                status="enabled",
                password_hash=hash_password("Apparel@2026"),
                note="本地兜底账号（飞书未配置时使用）",
            )
            self._save_file({bootstrap.username: bootstrap})
            return {bootstrap.username: bootstrap}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            str(name).lower(): Account(
                username=str(name),
                display=str(item.get("display") or name),
                role=str(item.get("role") or "member"),
                status=str(item.get("status") or "enabled"),
                quota=float(item.get("quota") or 0),
                password_hash=str(item.get("password_hash") or ""),
            )
            for name, item in raw.items()
        }

    def _save_file(self, accounts: dict[str, Account]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            acc.username: {
                "display": acc.display,
                "role": acc.role,
                "status": acc.status,
                "quota": acc.quota,
                "password_hash": acc.password_hash,
            }
            for acc in accounts.values()
        }
        self._file_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # -- 校验 --
    def authenticate(self, username: str, password: str) -> Account | None:
        accounts = self.load()
        account = accounts.get((username or "").strip().lower())
        if account is None or not account.active:
            return None
        if account.password_hash and verify_password(password, account.password_hash):
            return account
        if account.plain and hmac.compare_digest(password, account.plain):
            # 明文登录成功 → 升级成哈希并清空明文（失败也不影响本次登录）
            try:
                self.upgrade_to_hash(account, password)
            except Exception:  # noqa: BLE001
                pass
            return account
        return None

    def upgrade_to_hash(self, account: Account, password: str) -> None:
        hashed = hash_password(password)
        account.password_hash = hashed
        account.plain = ""
        if self.feishu_ready and account.record_id:
            client = FeishuBitable(self.cfg)
            table_id = getattr(self.cfg, "account_table_id", "")
            client.update_record(table_id, account.record_id, {"密码哈希": hashed, "明文密码": ""})
        elif not self.feishu_ready:
            self._save_file(self.accounts)

    # -- 管理（增删改，全部落到飞书表；没配飞书时才写本地文件） --
    def find(self, username: str, force: bool = False) -> Account | None:
        return self.load(force=force).get((username or "").strip().lower())

    def _write_fields(self, account: Account, fields: dict) -> None:
        """把字段写回飞书（或本地兜底文件），并让缓存立即失效。"""
        if self.feishu_ready and account.record_id:
            FeishuBitable(self.cfg).update_record(
                getattr(self.cfg, "account_table_id", ""), account.record_id, fields
            )
        else:
            self._save_file(self.accounts)
        self.loaded_at = 0.0
        self.load(force=True)

    def create(
        self,
        username: str,
        password: str,
        *,
        display: str = "",
        role: str = "member",
        status: str = "启用",
        quota: float = 0.0,
        note: str = "",
    ) -> Account:
        username = (username or "").strip()
        if not username:
            raise ValueError("账号不能为空")
        if self.find(username, force=True) is not None:
            raise ValueError(f"账号 {username} 已存在")
        fields = {
            "账号": username,
            "显示名": display or username,
            "密码哈希": hash_password(password),
            "明文密码": "",
            "角色": role or "member",
            "状态": status or "启用",
            "额度元": float(quota or 0),
            "备注": note or "",
            "创建时间": int(time.time() * 1000),
        }
        if self.feishu_ready:
            FeishuBitable(self.cfg).create_records(
                [fields], table_id=getattr(self.cfg, "account_table_id", "")
            )
            self.loaded_at = 0.0
            self.load(force=True)
        else:
            account = Account(
                username=username,
                display=fields["显示名"],
                role=fields["角色"],
                status=fields["状态"],
                quota=fields["额度元"],
                password_hash=fields["密码哈希"],
            )
            self.accounts[username.lower()] = account
            self._save_file(self.accounts)
        return self.find(username, force=True) or Account(username=username)

    def update(self, username: str, fields: dict) -> Account:
        """只更新传进来的字段：显示名/角色/状态/额度元/备注。"""
        account = self.find(username, force=True)
        if account is None:
            raise ValueError(f"账号 {username} 不存在")
        mapping = {
            "display": "显示名",
            "role": "角色",
            "status": "状态",
            "quota": "额度元",
            "note": "备注",
        }
        payload: dict = {}
        for key, label in mapping.items():
            if key not in fields or fields[key] is None:
                continue
            value = fields[key]
            if key == "quota":
                payload[label] = float(value)
                account.quota = float(value)
            else:
                payload[label] = str(value)
                if key == "display":
                    account.display = str(value)
                elif key == "role":
                    account.role = str(value)
                elif key == "status":
                    account.status = str(value)
                elif key == "note":
                    account.note = str(value)
        if payload:
            self._write_fields(account, payload)
        return self.find(username, force=True) or account

    def set_password(self, username: str, password: str) -> Account:
        if len(password or "") < 6:
            raise ValueError("密码至少 6 位")
        account = self.find(username, force=True)
        if account is None:
            raise ValueError(f"账号 {username} 不存在")
        hashed = hash_password(password)
        self._write_fields(account, {"密码哈希": hashed, "明文密码": ""})
        return self.find(username, force=True) or account

    def delete(self, username: str) -> None:
        account = self.find(username, force=True)
        if account is None:
            raise ValueError(f"账号 {username} 不存在")
        if self.feishu_ready and account.record_id:
            client = FeishuBitable(self.cfg)
            client.delete_record(getattr(self.cfg, "account_table_id", ""), account.record_id)
            self.loaded_at = 0.0
            self.load(force=True)
        else:
            self.accounts.pop(account.username.lower(), None)
            self._save_file(self.accounts)



# ------------------------------------------------------------------- 会话 --
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def make_session(username: str, secret: str, ttl: int = SESSION_TTL) -> str:
    body = _b64(
        json.dumps({"u": username, "exp": int(time.time()) + ttl}, ensure_ascii=False).encode(
            "utf-8"
        )
    )
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_session(cookie: str | None, secret: str) -> str | None:
    if not cookie or "." not in cookie or not secret:
        return None
    body, _, signature = cookie.rpartition(".")
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp") or 0) < time.time():
        return None
    return str(payload.get("u") or "") or None


LOGIN_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 生图工作台</title>
<style>
  :root{color-scheme:light dark}
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui;background:#f6f9f8;color:#0b1f1a}
  form{width:min(360px,92vw);background:#fff;border:1px solid #d6e2de;border-radius:14px;
    padding:24px;box-shadow:0 8px 24px rgba(11,31,26,.1);display:grid;gap:12px}
  h1{font-size:16px;margin:0 0 4px}
  p.sub{font-size:12px;color:#5a6b66;margin:0 0 8px}
  label{display:grid;gap:5px;font-size:12px;color:#5a6b66}
  input{width:100%;box-sizing:border-box;height:38px;padding:0 10px;border:1px solid #d6e2de;
    border-radius:10px;font:inherit}
  button{height:40px;border:0;border-radius:10px;background:#047857;color:#fff;
    font-weight:600;font-size:14px;cursor:pointer}
  button:hover{background:#065f46}
  .err{color:#b91c1c;font-size:12px}
</style>
<form method="post" action="/login">
  <h1>生图工作台</h1>
  <p class="sub">用飞书「账号」表里的账号登录</p>
  <label>账号<input name="username" autocomplete="username" required autofocus /></label>
  <label>密码<input name="password" type="password" autocomplete="current-password" required /></label>
  <input type="hidden" name="next" value="__NEXT__" />
  <button type="submit">登录</button>
  __ERROR__
</form></html>"""
