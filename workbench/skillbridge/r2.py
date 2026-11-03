"""把成品归档到 Cloudflare R2（S3 兼容）。默认关闭，需先在控制台开启 R2。

现状（已实测这个账号）：
  * API Token 有效：`/accounts/{id}/tokens/verify` → active
  * `GET /accounts/{id}/r2/buckets` → **10042 Please enable R2 through the Cloudflare Dashboard**
所以本模块在你开通 R2 并建好 bucket 之前会明确报这条错，而不是静默失败。

用法：
    # 1) 先确认账号状态（只需要 CF_API_TOKEN）
    python -m skillbridge.r2 --check

    # 2) 开通 R2 并建 bucket 后，上传一个文件
    python -m skillbridge.r2 --bucket apparel --key out/demo.zip --file data/jobs/xxx/outputs/xxx.zip

签名使用标准 AWS SigV4（R2 的 S3 API 要求），只用标准库实现。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from .config import load_env


@dataclass
class R2Config:
    account_id: str = ""
    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    endpoint: str = ""
    api_token: str = ""

    @property
    def host(self) -> str:
        if self.endpoint:
            return urlparse(self.endpoint).netloc
        return f"{self.account_id}.r2.cloudflarestorage.com"

    def ready(self) -> tuple[bool, str]:
        missing = [
            name
            for name, value in (
                ("R2_ACCOUNT_ID", self.account_id),
                ("R2_ACCESS_KEY_ID", self.access_key),
                ("R2_SECRET_ACCESS_KEY", self.secret_key),
                ("R2_BUCKET", self.bucket),
            )
            if not value
        ]
        if missing:
            return False, f"缺少 {', '.join(missing)}（填在 .env 里）"
        return True, "就绪"


def load_r2(env: dict | None = None) -> R2Config:
    env = env or load_env()
    return R2Config(
        account_id=str(env.get("R2_ACCOUNT_ID", "")).strip(),
        access_key=str(env.get("R2_ACCESS_KEY_ID", "")).strip(),
        secret_key=str(env.get("R2_SECRET_ACCESS_KEY", "")).strip(),
        bucket=str(env.get("R2_BUCKET", "")).strip(),
        endpoint=str(env.get("R2_S3_ENDPOINT", "")).strip(),
        api_token=str(env.get("CLOUDFLARE_API_TOKEN", "")).strip(),
    )


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + secret).encode("utf-8"), date)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _encode_key(key: str) -> str:
    return quote(key, safe="/-_.~")


def put_object(cfg: R2Config, key: str, data: bytes, *, content_type: str = "application/zip") -> str:
    """SigV4 PUT 到 R2，返回对象 URL。失败抛 RuntimeError（带响应体）。"""
    ok, reason = cfg.ready()
    if not ok:
        raise RuntimeError(reason)

    region = "auto"
    service = "s3"
    now = time.gmtime()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", now)
    date_stamp = time.strftime("%Y%m%d", now)
    payload_hash = hashlib.sha256(data).hexdigest()
    canonical_uri = f"/{cfg.bucket}/{_encode_key(key)}"
    host = cfg.host

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "content-type": content_type,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(cfg.secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={cfg.access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    url = f"https://{host}{canonical_uri}"
    request = urllib.request.Request(url, data=data, method="PUT")
    for name, value in headers.items():
        if name != "host":
            request.add_header(name, value)
    request.add_header("Authorization", authorization)

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"上传失败 HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"上传失败 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"上传失败（网络）：{exc.reason}") from exc
    return url


def check_account(cfg: R2Config) -> str:
    """用 API Token 查账号与 R2 状态，返回人话结论。"""
    if not cfg.api_token or not cfg.account_id:
        return "缺少 CLOUDFLARE_API_TOKEN / R2_ACCOUNT_ID"
    base = f"https://api.cloudflare.com/client/v4/accounts/{cfg.account_id}"
    request = urllib.request.Request(f"{base}/r2/buckets")
    request.add_header("Authorization", f"Bearer {cfg.api_token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"R2 可用，返回 {response.status}（可以建 bucket 了）"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if "10042" in body:
            return "R2 还没开通：控制台 → R2 → 同意开通（需绑卡）后即可用"
        return f"R2 查询失败 HTTP {exc.code}: {body[:200]}"
    except urllib.error.URLError as exc:
        return f"R2 查询失败（网络）：{exc.reason}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloudflare R2 归档工具")
    parser.add_argument("--check", action="store_true", help="只检查账号与 R2 状态")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--file", default="")
    args = parser.parse_args(argv)

    cfg = load_r2()
    if args.bucket:
        cfg.bucket = args.bucket
    print(f"账号：{cfg.account_id or '(未配置)'}  endpoint：{cfg.endpoint or cfg.host}")
    print(f"检查：{check_account(cfg)}")
    if args.check:
        return 0
    if not (args.file and args.key):
        print("需要 --file 与 --key（或用 --check 只做体检）")
        return 2
    data = Path(args.file).read_bytes()
    url = put_object(cfg, args.key, data)
    print(f"已上传：{url}（{len(data)} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
