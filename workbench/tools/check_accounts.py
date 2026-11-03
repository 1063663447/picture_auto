"""账号登录 + 飞书定价表 自检。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import auth, feishu, pricing  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402

env = load_env()
cfg = load_config(None, env)
fcfg = feishu.load_config(cfg, env)
print("feishu ready:", fcfg.ready(), "price_table:", fcfg.price_table_id,
      "account_table:", fcfg.account_table_id)

client = feishu.FeishuBitable(fcfg)

# 1. 定价表
items = client.search_table(fcfg.price_table_id)
print("price rows:", len(items))
print("sample:", json.dumps(items[0]["fields"], ensure_ascii=False)[:300] if items else "-")
models = pricing.Pricer.parse_feishu_rows(items)
print("parsed models:", len(models))
for name in ("gpt-image-2", "gpt-image-2-auto", "gpt-image-2-native"):
    print(f"  {name} -> {models.get(name)!r}")

p = pricing.Pricer()
ok = p.refresh_from_feishu(fcfg, force=True)
print("refresh ok:", ok, "source:", p.source, "currency:", p.currency, "models:", len(p.raw))
print("price gpt-image-2 @1024x1536:", p.price("gpt-image-2", "1024x1536").label())
print("price gpt-image-2-auto @2048x2048:", p.price("gpt-image-2-auto", "2048x2048").label())
print("estimate:", p.estimate("gpt-image-2", 6, "1024x1536"))

# 2. 账号表
store = auth.AccountStore(fcfg, Path(cfg["data_dir"]))
accounts = store.load(force=True)
print("accounts source:", store.source, "count:", len(accounts))
for acc in accounts.values():
    print("  ", acc.username, acc.role, acc.status, "hash?", bool(acc.password_hash),
          "plain?", bool(acc.plain))

# 3. 密码哈希
h = auth.hash_password("Apparel@2026")
print("verify ok:", auth.verify_password("Apparel@2026", h),
      "verify bad:", auth.verify_password("wrong", h))

# 4. 登录
good = store.authenticate("admin", "Apparel@2026")
print("login good:", bool(good), good.role if good else "")
print("login bad:", store.authenticate("admin", "nope-123"))
print("login ghost:", store.authenticate("ghost", "Apparel@2026"))

# 5. 会话签名
cookie = auth.make_session("admin", "secret-x")
print("session:", auth.read_session(cookie, "secret-x"),
      "tamper:", auth.read_session(cookie + "0", "secret-x"),
      "wrong-key:", auth.read_session(cookie, "other"))
