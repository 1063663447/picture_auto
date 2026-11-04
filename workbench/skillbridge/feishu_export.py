"""从飞书多维表格回拉历史成品：不依赖本机 data/。

场景：Render 免费实例磁盘是临时的，重启后 data/ 清空；但每条成品都在飞书里（含成品图附件）。
所以「历史记录」= 飞书表格，「批量打包历史图片」= 从飞书把附件下载回来打 ZIP。

提供：
  * list_records(limit, job_id, color)  → 记录清单（成品/颜色/模型/金额/时间/附件数/record_id）
  * download_attachment(file_token)     → 附件字节
  * build_zip(records, out_path)        → 下载选中的成品图，按 成品 字段命名打包
"""

from __future__ import annotations

import io
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .feishu import BASE, FeishuBitable, FeishuConfig

SAFE = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")


def _flat(value) -> str:
    """把飞书字段值压成字符串（文本可能是分段数组，附件是对象数组）。"""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if item.get("text") is not None:
                    parts.append(str(item["text"]))
                elif item.get("file_token"):
                    parts.append("[附件]")
                elif item.get("link"):
                    parts.append(str(item["link"]))
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("link") or "")
    return str(value)


def list_records(
    cfg: FeishuConfig,
    *,
    limit: int = 200,
    job_id: str = "",
    color: str = "",
) -> list[dict]:
    """列出飞书表格里的成品记录（按时间倒序）。"""
    client = FeishuBitable(cfg)
    token = client.token()
    payload: dict = {"automatic_fields": False}
    conditions = []
    if job_id:
        conditions.append(
            {"field_name": "任务ID", "operator": "is", "value": [job_id]}
        )
    if color:
        conditions.append({"field_name": "颜色", "operator": "is", "value": [color]})
    if conditions:
        payload["filter"] = {"conjunction": "and", "conditions": conditions}

    records: list[dict] = []
    page_token = ""
    while len(records) < limit:
        url = (
            f"{BASE}/bitable/v1/apps/{cfg.app_token}/tables/{cfg.table_id}"
            f"/records/search?page_size={min(100, limit - len(records))}"
        )
        if page_token:
            url += f"&page_token={urllib.parse.quote(page_token)}"
        data = _post(url, token, payload)
        items = (data.get("data") or {}).get("items") or []
        for item in items:
            fields = item.get("fields") or {}
            attachments = fields.get("成品图") or []
            token_ = ""
            if isinstance(attachments, list) and attachments and isinstance(attachments[0], dict):
                token_ = str(attachments[0].get("file_token") or "")
            records.append(
                {
                    "record_id": item.get("record_id"),
                    "name": _flat(fields.get("成品")) or str(item.get("record_id")),
                    "job_id": _flat(fields.get("任务ID")),
                    "color": _flat(fields.get("颜色")),
                    "model": _flat(fields.get("模型")),
                    "size": _flat(fields.get("尺寸")),
                    "status": _flat(fields.get("状态")),
                    "amount": fields.get("金额元"),
                    "time": fields.get("时间"),
                    "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
                    "file_token": token_,
                }
            )
        page_token = (data.get("data") or {}).get("page_token") or ""
        if not page_token or not items:
            break
    return records


def _post(url: str, token: str, payload: dict) -> dict:
    import json as _json

    request = urllib.request.Request(
        url, data=_json.dumps(payload).encode("utf-8"), method="POST"
    )
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return _json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"飞书查询失败 HTTP {exc.code}: {body[:200]}") from exc


def tmp_download_urls(cfg: FeishuConfig, file_tokens: list[str]) -> dict[str, str]:
    """把 file_token 换成飞书签发的临时下载直链（浏览器直接下载，不经过 Render，省流量）。"""
    tokens = [t for t in dict.fromkeys(file_tokens) if t]
    if not tokens:
        return {}
    client = FeishuBitable(cfg)
    result: dict[str, str] = {}
    for start in range(0, len(tokens), 50):
        chunk = tokens[start : start + 50]
        # 注意：这个接口要求 file_tokens **重复传参**（file_tokens=a&file_tokens=b），
        # 用逗号拼成一个值会返回空数组（实测）。
        query = urllib.parse.urlencode([("file_tokens", t) for t in chunk])
        url = f"{BASE}/drive/v1/medias/batch_get_tmp_download_url?{query}"
        request = urllib.request.Request(url)
        request.add_header("Authorization", f"Bearer {client.token()}")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                import json as _json

                payload = _json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"飞书直链获取失败 HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
            ) from exc
        for item in (payload.get("data") or {}).get("tmp_download_urls") or []:
            token_ = str(item.get("file_token") or "")
            link = str(item.get("tmp_download_url") or "")
            if token_ and link:
                result[token_] = link
    return result


def download_attachment(cfg: FeishuConfig, file_token: str) -> bytes:
    client = FeishuBitable(cfg)
    url = f"{BASE}/drive/v1/medias/{urllib.parse.quote(file_token)}/download"
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {client.token()}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"附件下载失败 HTTP {exc.code}: {detail}") from exc


def _safe_name(text: str, fallback: str) -> str:
    name = SAFE.sub("-", (text or "").strip()).strip("-")
    return name or fallback


def _guess_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def build_zip(cfg: FeishuConfig, records: list[dict], *, folder: str = "feishu-export") -> bytes:
    """下载选中记录的成品图并打包。失败的记录会写进 _missing.txt。"""
    picked = [r for r in records if r.get("file_token")]
    if not picked:
        raise RuntimeError("选中的记录里没有可下载的成品图附件")
    buffer = io.BytesIO()
    missing: list[str] = []
    used: dict[str, int] = {}
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for record in picked:
            name = _safe_name(record.get("name"), str(record.get("record_id")))
            try:
                data = download_attachment(cfg, str(record["file_token"]))
            except Exception as exc:  # noqa: BLE001
                missing.append(f"{name}: {exc}")
                continue
            ext = _guess_ext(data)
            count = used.get(name, 0) + 1
            used[name] = count
            suffix = "" if count == 1 else f"-{count}"
            archive.writestr(f"{folder}/{name}{suffix}{ext}", data)
        if missing:
            archive.writestr(f"{folder}/_missing.txt", "\n".join(missing))
    return buffer.getvalue()
