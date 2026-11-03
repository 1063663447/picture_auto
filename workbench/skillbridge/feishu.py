"""飞书 / Lark 多维表格（Bitable）作为记录库 —— 带图片附件。

每条记录对应一个成品（默认 `per: "task"`），字段见 `FIELD_SCHEMA`；
「成品图 / 参考图 / 原图」是**附件字段**：图片会先传到飞书云空间换 `file_token`，再挂到记录上，
所以在飞书里能直接看到成品和它用的色卡，不需要 zip、也不依赖本机磁盘。

表结构可用 `deploy/setup_feishu_table.py` 一键整理（幂等）。

凭据（.env）：FEISHU_APP_ID / FEISHU_APP_SECRET
表格定位（config.json → feishu）：app_token（/base/ 后面那段）+ table_id（?table= 那段）
应用需要：bitable:app（必须）、drive:drive（上传附件必须），并加为该多维表格的协作者（可编辑）。
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

BASE = "https://open.feishu.cn/open-apis"

#: 字段名 → 飞书字段类型（1 文本 / 2 数字 / 5 日期 / 17 附件）
FIELD_SCHEMA: list[tuple[str, int]] = [
    ("成品", 1),
    ("任务ID", 1),
    ("时间", 5),
    ("账号", 1),
    ("款式", 1),
    ("颜色", 1),
    ("模型", 1),
    ("尺寸", 1),
    ("张数", 2),
    ("单价元", 2),
    ("金额元", 2),
    ("接口", 1),
    ("状态", 1),
    ("备注", 1),
    ("成品图", 17),
    ("参考图", 17),
    ("原图", 17),
]
ATTACHMENT_FIELDS = ("成品图", "参考图", "原图")


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    app_token: str = ""
    table_id: str = ""
    price_table_id: str = ""
    account_table_id: str = ""
    per: str = "task"
    upload_images: bool = True
    upload_source: bool = False
    enabled: bool = True
    extra: dict = field(default_factory=dict)

    def ready(self) -> tuple[bool, str]:
        missing = [
            label
            for label, value in (
                ("FEISHU_APP_ID", self.app_id),
                ("FEISHU_APP_SECRET", self.app_secret),
                ("feishu.app_token", self.app_token),
                ("feishu.table_id", self.table_id),
            )
            if not value
        ]
        if missing:
            return False, f"缺少 {', '.join(missing)}"
        return True, "就绪"


def _json_request(method: str, url: str, token: str = "", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"code": exc.code, "msg": body[:300]}


class FeishuBitable:
    def __init__(self, cfg: FeishuConfig) -> None:
        self.cfg = cfg
        self._token = ""
        self._token_at = 0.0

    # ------------------------------------------------------------- token --
    def token(self, force: bool = False) -> str:
        if self._token and not force and time.time() - self._token_at < 1500:
            return self._token
        data = _json_request(
            "POST",
            f"{BASE}/auth/v3/tenant_access_token/internal",
            payload={"app_id": self.cfg.app_id, "app_secret": self.cfg.app_secret},
        )
        if data.get("code") != 0:
            raise RuntimeError(
                f"飞书鉴权失败 code={data.get('code')} msg={data.get('msg')}（检查 app_id/app_secret）"
            )
        self._token = str(data.get("tenant_access_token") or "")
        self._token_at = time.time()
        if not self._token:
            raise RuntimeError("飞书未返回 tenant_access_token")
        return self._token

    def _api(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = _json_request(method, f"{BASE}{path}", self.token(), payload)
        if data.get("code") != 0:
            raise RuntimeError(f"飞书接口失败 {path} code={data.get('code')} msg={data.get('msg')}")
        return data.get("data") or {}

    # ------------------------------------------------------------ fields --
    def existing_fields(self) -> dict[str, int]:
        data = self._api(
            "GET",
            f"/bitable/v1/apps/{self.cfg.app_token}/tables/{self.cfg.table_id}/fields?page_size=100",
        )
        return {
            str(item.get("field_name")): int(item.get("type") or 0)
            for item in data.get("items") or []
        }

    def ensure_fields(self) -> list[str]:
        have = self.existing_fields()
        created: list[str] = []
        for name, ftype in FIELD_SCHEMA:
            if name in have:
                continue
            self._api(
                "POST",
                f"/bitable/v1/apps/{self.cfg.app_token}/tables/{self.cfg.table_id}/fields",
                {"field_name": name, "type": ftype},
            )
            created.append(name)
        return created

    # -------------------------------------------------------- attachments --
    def upload_file(self, path: Path) -> str:
        boundary = "----feishu" + uuid.uuid4().hex
        body = bytearray()
        meta = {
            "file_name": path.name,
            "parent_type": "bitable_file",
            "parent_node": self.cfg.app_token,
            "size": str(path.stat().st_size),
        }
        for key, value in meta.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}"
            "\r\n\r\n"
        ).encode()
        body += path.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()

        request = urllib.request.Request(
            f"{BASE}/drive/v1/medias/upload_all", data=bytes(body), method="POST"
        )
        request.add_header("Authorization", f"Bearer {self.token()}")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"附件上传失败 HTTP {exc.code}: {detail}") from exc
        if data.get("code") != 0:
            raise RuntimeError(
                f"附件上传失败 code={data.get('code')} msg={data.get('msg')}"
                "（多半是应用没开 drive:drive 权限）"
            )
        return str((data.get("data") or {}).get("file_token") or "")

    def attach(self, path: Path) -> list[dict]:
        token = self.upload_file(path)
        return [{"file_token": token}] if token else []

    # ----------------------------------------------------------- records --
    def search_table(self, table_id: str, page_size: int = 200, max_pages: int = 10) -> list[dict]:
        """读任意表的全部行（自动翻页）。返回 [{record_id, fields}]。"""
        items: list[dict] = []
        page_token = ""
        for _ in range(max_pages):
            query = f"?page_size={page_size}"
            if page_token:
                query += f"&page_token={page_token}"
            data = self._api(
                "POST",
                f"/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/records/search{query}",
                {"automatic_fields": False},
            )
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = str(data.get("page_token") or "")
            if not page_token:
                break
        return items

    def update_record(self, table_id: str, record_id: str, fields: dict) -> dict:
        return self._api(
            "PUT",
            f"/bitable/v1/apps/{self.cfg.app_token}/tables/{table_id}/records/{record_id}",
            {"fields": fields},
        )

    def create_records(self, rows: list[dict]) -> list[str]:
        ids: list[str] = []
        for start in range(0, len(rows), 100):
            chunk = rows[start : start + 100]
            data = self._api(
                "POST",
                f"/bitable/v1/apps/{self.cfg.app_token}/tables/{self.cfg.table_id}/records/batch_create",
                {"records": [{"fields": row} for row in chunk]},
            )
            for item in data.get("records") or []:
                ids.append(str(item.get("record_id") or ""))
        return ids


def _ms(ts: float | None) -> int:
    return int((ts or time.time()) * 1000)


def build_rows(job, size: str, unit: float | None, currency: str, *, per: str = "task") -> list[dict]:
    """把任务转成行（纯函数，不含附件；附件由 service 上传后补进去）。"""
    params = job.params or {}
    stats = job.counts()
    done_total = stats.get("done", 0) + stats.get("skipped", 0)
    finished = _ms(max((t.finished_at or 0) for t in job.tasks) if job.tasks else None)

    def money(count: int) -> float | None:
        return round(float(unit) * count, 4) if unit is not None else None

    if per == "job":
        return [
            {
                "_task_id": "",
                "成品": f"{params.get('garment_name', '')} · {done_total} 张",
                "任务ID": job.id,
                "时间": finished,
                "账号": str(params.get("account") or "anonymous"),
                "款式": str(params.get("garment_name") or ""),
                "颜色": "、".join(params.get("colors") or []),
                "模型": str(params.get("model") or ""),
                "尺寸": str(size or ""),
                "张数": done_total,
                "单价元": (float(unit) if unit is not None else None),
                "金额元": money(done_total),
                "接口": str(params.get("provider") or ""),
                "状态": job.status,
                "备注": (params.get("extra_requirements") or "")[:500],
            }
        ]

    rows: list[dict] = []
    for task in job.tasks:
        if task.status not in {"done", "failed", "canceled"}:
            continue
        rows.append(
            {
                "_task_id": task.id,
                "成品": f"{task.item} / {task.color_name}",
                "任务ID": job.id,
                "时间": _ms(task.finished_at),
                "账号": str(params.get("account") or "anonymous"),
                "款式": str(params.get("garment_name") or ""),
                "颜色": task.color_name,
                "模型": str(params.get("model") or ""),
                "尺寸": str(size or ""),
                "张数": 1 if task.status == "done" else 0,
                "单价元": (float(unit) if unit is not None else None),
                "金额元": money(1) if task.status == "done" else 0,
                "接口": str(params.get("provider") or ""),
                "状态": task.status,
                "备注": (task.error or "")[:500],
            }
        )
    return rows


def load_config(config: dict, env: dict) -> FeishuConfig:
    block = config.get("feishu") or {}
    return FeishuConfig(
        app_id=str(env.get(str(block.get("app_id_env") or "FEISHU_APP_ID"), "")).strip(),
        app_secret=str(env.get(str(block.get("app_secret_env") or "FEISHU_APP_SECRET"), "")).strip(),
        app_token=str(block.get("app_token") or "").strip(),
        table_id=str(block.get("table_id") or "").strip(),
        price_table_id=str(block.get("price_table_id") or "").strip(),
        account_table_id=str(block.get("account_table_id") or "").strip(),
        per=str(block.get("per") or "task"),
        upload_images=bool(block.get("upload_images", True)),
        upload_source=bool(block.get("upload_source", False)),
        enabled=bool(block.get("enabled", True)),
    )


# ------------------------------------------------------------------ CLI ------
def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from .config import load_config as load_wb_config, load_env

    parser = argparse.ArgumentParser(description="飞书多维表格：体检 / 建字段 / 补同步历史任务")
    parser.add_argument("--check", action="store_true", help="检查凭据与表格连通性")
    parser.add_argument("--init-fields", action="store_true", help="自动补齐字段")
    parser.add_argument("--sync-job", default="", help="把一个历史任务补写进表格（含图片）")
    args = parser.parse_args(argv)

    env = load_env()
    config = load_wb_config(None, env)
    cfg = load_config(config, env)
    ok, reason = cfg.ready()
    print(f"配置：app_token={cfg.app_token or '(空)'} table_id={cfg.table_id or '(空)'} per={cfg.per}")
    if not ok:
        print(f"[x] {reason}\n    → 按 workbench/deploy/feishu.md 配好 .env 与 config.json 再来")
        return 2
    client = FeishuBitable(cfg)
    try:
        client.token()
        fields = client.existing_fields()
        print(f"[ok] 鉴权通过，表格现有 {len(fields)} 个字段")
        missing = [n for n, _ in FIELD_SCHEMA if n not in fields]
        if missing:
            print(f"[!] 缺字段：{missing}")
        if args.init_fields:
            print(f"[ok] 新建字段：{client.ensure_fields() or '（都已有）'}")
        if args.sync_job:
            from .service import Workbench

            bench = Workbench()
            job = bench.get_job(args.sync_job)
            count = bench.sync_to_feishu(job)
            print(f"[ok] 写入 {count} 行（附件已按配置上传）")
            print(f"     表格：https://feishu.cn/base/{cfg.app_token}?table={cfg.table_id}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[x] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
