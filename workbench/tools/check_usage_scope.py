"""Offline account-isolation regression: python workbench/tools/check_usage_scope.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from skillbridge import auth  # noqa: E402
from skillbridge.api import create_app  # noqa: E402
from skillbridge.pricing import Pricer, UsageLedger  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402


class OfflineWorkbench(Workbench):
    """Keep real payload methods; skip provider, gate, skill and sync startup."""

    def __init__(self, config_path: Path) -> None:
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.env = {}
        self.data_dir = Path(self.config["data_dir"])
        self.feishu_config = None
        self.pricer = Pricer(
            Path(self.config["pricing_file"]),
            provider_prices={"offline": {"priced": 0.65}},
        )
        self.usage = UsageLedger(self.data_dir, self.pricer)
        self.accounts = auth.AccountStore(None, self.data_dir)
        self.providers = {"offline": SimpleNamespace(cfg={"model": "priced"})}

    def provider_payload(self) -> list[dict]:
        return [{"name": "offline", "label": "Offline fixture"}]


class UsageScopeChecks(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(
            prefix="usage-scope-", dir=os.environ.get("PI_SCRATCH_DIR")
        )
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.secret = "offline-usage-scope-session"
        self.token = "offline-usage-scope-token"
        env_patch = patch.dict(os.environ, {"WORKBENCH_SESSION_SECRET": self.secret})
        env_patch.start()
        self.addCleanup(env_patch.stop)

        # TestClient uses its own in-process transport. Any external HTTP fails.
        self.network_guards = []
        for target in (
            "httpx.HTTPTransport.handle_request",
            "httpx.AsyncHTTPTransport.handle_async_request",
            "skillbridge.api.Workbench",
        ):
            guard = patch(target, side_effect=AssertionError("External HTTP/startup disabled"))
            self.network_guards.append(guard.start())
            self.addCleanup(guard.stop)

        pricing_path = root / "pricing.json"
        pricing_path.write_text(json.dumps({
            "currency": "USD", "models": {"priced": 0.065, "unpriced": None},
        }), encoding="utf-8")
        config_path = root / "config.json"
        config_path.write_text(json.dumps({
            "data_dir": str(root), "pricing_file": str(pricing_path),
            "server": {"auth_token": self.token, "session_secret": self.secret,
                       "login_required": True},
        }), encoding="utf-8")
        (root / "accounts.json").write_text(json.dumps({
            "Alice": {"role": "member"}, "bOB": {"role": "member"},
            "AdMiN": {"role": "admin"}, "Nobody": {"role": "member"},
        }), encoding="utf-8")
        self.bench = OfflineWorkbench(config_path)
        self.ledger = self.bench.usage
        today = datetime.combine(date.today(), time(12))
        self.today = today.timestamp()
        yesterday = (today - timedelta(days=1)).timestamp()
        # Account, count, timestamp, model, provider. Order is deliberately mixed.
        fixtures = [
            ("alice-old", "Alice", 2, yesterday, "priced", ""),
            ("bob-old", "bOB", 3, yesterday, "priced", ""),
            ("admin-old", "AdMiN", 1, yesterday, "priced", ""),
            ("alice-today", "Alice", 4, self.today, "priced", ""),
            ("alice-override", "aLiCe", 1, self.today, "priced", "offline"),
            ("bob-unpriced", "BOB", 2, self.today, "unpriced", ""),
            ("bob-today", "bob", 1, self.today, "priced", ""),
            ("admin-today", "ADMIN", 2, self.today, "priced", ""),
            ("admin-unpriced", "admin", 1, self.today, "unpriced", ""),
            ("anon-today", "", 2, self.today, "priced", ""),
            ("anon-empty", "", 1, yesterday, "unpriced", ""),
            ("anon-missing", "", 1, self.today, "priced", ""),
            ("anon-case", "AnOnYmOuS", 1, self.today, "unpriced", ""),
            ("anon-null", "", 1, self.today, "unpriced", ""),
        ]
        for job_id, account, images, at, model, provider in fixtures:
            with patch("skillbridge.pricing.time.time", return_value=at):
                self.ledger.record(job_id=job_id, account=account, images=images,
                                   model=model, provider=provider, detail=job_id)
        # Include historical rows predating record()'s anonymous normalization.
        self.ledger.entries[10]["account"] = ""
        self.ledger.entries[11].pop("account")
        self.ledger.entries[13]["account"] = None
        self.ledger._save()
        self.ledger.load()
        self.ids = {
            "alice": ["alice-old", "alice-today", "alice-override"],
            "bob": ["bob-old", "bob-unpriced", "bob-today"],
            "admin": ["admin-old", "admin-today", "admin-unpriced"],
            "anonymous": ["anon-today", "anon-empty", "anon-missing", "anon-case", "anon-null"],
        }
        self.all_ids = [item[0] for item in fixtures]
        app = create_app(str(config_path))
        app.state.wb = self.bench
        # Deliberately avoid the context manager: lifespan must never run.
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def tearDown(self) -> None:
        for guard in self.network_guards:
            guard.assert_not_called()

    def sign_in(self, username: str) -> None:
        self.client.cookies.clear()
        self.client.cookies.set("wb_session", auth.make_session(username, self.secret))

    def get_json(self, path: str) -> dict:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_scope(self, account, total, today, jobs, unpriced, ids) -> None:
        result = self.ledger.summary(account=account)
        self.assertEqual(set(result), {
            "currency", "today", "total", "unpriced_images", "jobs", "accounts", "recent",
        })
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["total"], {"images": total[0], "cost": total[1]})
        self.assertEqual(result["today"], {"images": today[0], "cost": today[1]})
        self.assertEqual(result["jobs"], jobs)
        self.assertEqual(result["unpriced_images"], unpriced)
        self.assertEqual([row["job_id"] for row in result["recent"]], list(reversed(ids)))
        buckets = result["accounts"]
        self.assertEqual(sum(row["images"] for row in buckets), total[0])
        self.assertEqual(round(sum(row["cost"] for row in buckets), 4), total[1])
        self.assertEqual(sum(row["jobs"] for row in buckets), jobs)
        self.assertEqual(sum(row["unpriced_images"] for row in buckets), unpriced)
        nested_ids = {item["job_id"] for row in buckets for item in row["recent"]}
        self.assertEqual(nested_ids, set(ids))
        if account is not None:
            normalized = (account or "anonymous").casefold()
            self.assertTrue(all(row["account"].casefold() == normalized for row in buckets))
        self.assertEqual(self.ledger.by_account(account=account), buckets)
        self.assertEqual(self.bench.usage_payload(account=account), result)
        prices = self.bench.pricing_payload(account=account)
        self.assertEqual(prices["usage"], result)
        self.assertEqual(prices["default_model"], "priced")
        self.assertEqual({row["model"]: row["unit"] for row in prices["models"]},
                         {"priced": 0.065, "unpriced": None})

    def test_ledger_and_service_scopes(self) -> None:
        for account, total, today, jobs, unpriced in (
            (None, (23, 1.69), (16, 1.3), 14, 6),
            ("ALICE", (7, 1.04), (5, 0.91), 3, 0),
            ("aLiCe", (7, 1.04), (5, 0.91), 3, 0),
            ("BoB", (6, 0.26), (3, 0.065), 3, 2),
            ("ADMIN", (4, 0.195), (3, 0.13), 3, 1),
        ):
            with self.subTest(account=account):
                ids = self.all_ids if account is None else self.ids[account.casefold()]
                self.assert_scope(account, total, today, jobs, unpriced, ids)
        self.assertEqual(self.ledger.summary(), self.ledger.summary(account=None))
        self.assertEqual(self.bench.usage_payload(), self.ledger.summary())
        self.assertEqual(self.bench.pricing_payload()["usage"], self.ledger.summary())
        self.assertEqual(self.ledger.by_account(), self.ledger.summary()["accounts"])
        costs = {row["job_id"]: row["cost"] for row in self.ledger.summary("alice")["recent"]}
        self.assertEqual(costs, {"alice-old": 0.13, "alice-today": 0.26, "alice-override": 0.65})

    def test_anonymous_and_unknown_scopes(self) -> None:
        for account in ("", "anonymous", "ANONYMOUS"):
            with self.subTest(account=account):
                self.assert_scope(account, (6, 0.195), (5, 0.195), 5, 3, self.ids["anonymous"])
        for account in ("Nobody", "unknown-account"):
            with self.subTest(account=account):
                self.assert_scope(account, (0, 0.0), (0, 0.0), 0, 0, [])

    def test_recent_limits_follow_account_filter(self) -> None:
        for number in range(25):
            self.ledger.record(job_id=f"bob-extra-{number}", account="Bob", model="priced", images=1)
        self.assertEqual([row["job_id"] for row in self.ledger.summary("alice")["recent"]],
                         list(reversed(self.ids["alice"])))
        for number in range(25):
            self.ledger.record(job_id=f"alice-extra-{number}", account="aLiCe", model="priced", images=1)
        self.ledger.record(job_id="bob-last", account="Bob", model="priced", images=1)
        result = self.ledger.summary("ALICE")
        self.assertEqual([row["job_id"] for row in result["recent"]],
                         [f"alice-extra-{number}" for number in range(24, 4, -1)])
        bucket = next(row for row in result["accounts"] if row["account"] == "aLiCe")
        self.assertEqual([row["job_id"] for row in bucket["recent"]],
                         [f"alice-extra-{number}" for number in range(24, 14, -1)])
        self.assertEqual(len(self.ledger.summary()["recent"]), 20)

    def test_http_current_account_and_roles(self) -> None:
        for username, account, admin in (
            ("ALICE", "Alice", False), ("BOB", "bOB", False),
            ("ADMIN", "AdMiN", True), ("NOBODY", "Nobody", False),
        ):
            with self.subTest(username=username):
                self.sign_in(username)
                for path in ("/api/usage", "/api/usage?account=bob"):
                    self.assertEqual(self.get_json(path),
                                     {"ok": True, **self.ledger.summary(account=account)})
                prices = self.get_json("/api/pricing?account=Alice")
                scope = None if admin else account
                self.assertEqual(prices, {"ok": True, **self.bench.pricing_payload(account=scope)})
                if not admin:
                    for path in ("/api/accounts", "/api/admin/overview"):
                        self.assertEqual(self.client.get(path).status_code, 403)

    def test_http_admin_aggregates_stay_global(self) -> None:
        self.sign_in("ADMIN")
        accounts = self.get_json("/api/accounts")
        self.assertEqual(accounts, self.bench.accounts_payload())
        self.assertEqual(accounts["usage_source"], "ledger")
        self.assertEqual(sum(row["images"] for row in accounts["accounts"]), 23)
        self.assertEqual(round(sum(row["cost"] for row in accounts["accounts"]), 4), 1.69)
        overview = self.get_json("/api/admin/overview")
        self.assertEqual(overview, {"ok": True, **self.bench.admin_overview()})
        self.assertEqual(overview["totals"]["images"], 23)
        self.assertEqual(overview["totals"]["cost"], 1.69)
        self.assertEqual(overview["totals"]["jobs"], 14)
        self.assertEqual(overview["totals"]["today"]["images"], 16)
        self.assertEqual(overview["totals"]["today"]["cost"], 1.3)
        self.assertEqual(overview["totals"]["yesterday"]["images"], 7)
        self.assertEqual(overview["totals"]["yesterday"]["cost"], 0.39)
        self.assertEqual({row["job_id"] for row in overview["recent"]}, set(self.all_ids))
        self.assertTrue({"alice", "bob", "admin", "anonymous"}.issubset(
            {row["username"].casefold() for row in accounts["accounts"]}))

    def test_http_no_session_and_machine_token(self) -> None:
        for path in ("/api/usage", "/api/pricing", "/api/accounts", "/api/admin/overview"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.bench.config["server"]["login_required"] = False
        self.assertEqual(self.get_json("/api/usage"), {"ok": True, **self.ledger.summary(account="")})
        self.assertEqual(self.get_json("/api/pricing")["usage"], self.ledger.summary(account=""))
        self.assertEqual(self.client.get("/api/accounts").status_code, 403)
        self.bench.config["server"]["login_required"] = True
        response = self.client.get("/api/usage", headers={"x-workbench-token": self.token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, **self.ledger.summary(account="token")})
        response = self.client.get("/api/pricing", headers={"x-workbench-token": self.token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usage"], self.ledger.summary())


if __name__ == "__main__":
    unittest.main(verbosity=2)
