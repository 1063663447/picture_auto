"""删除指定任务ID的飞书记录（清理测试行）。

用法：python tools/cleanup_feishu_jobs.py 20261103-160359-3147 [more ids...]
      python tools/cleanup_feishu_jobs.py --dry-run <id>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillbridge import feishu  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402
from skillbridge.feishu import BASE, _json_request  # noqa: E402


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    dry = "--dry-run" in argv
    wanted = {a for a in argv if not a.startswith("--")}
    if not wanted:
        print("用法：python tools/cleanup_feishu_jobs.py [--dry-run] <任务ID> ...")
        return 2

    env = load_env()
    cfg = load_config(None, env)
    fcfg = feishu.load_config(cfg, env)
    client = feishu.FeishuBitable(fcfg)
    rows = client.search_table(fcfg.table_id, page_size=200, max_pages=10)

    doomed = []
    for item in rows:
        fields = item.get("fields") or {}
        tid = fields.get("任务ID")
        if isinstance(tid, list) and tid:
            tid = tid[0].get("text")
        if str(tid or "") in wanted:
            doomed.append(item.get("record_id"))
    print(f"匹配 {len(doomed)} 行（表内共 {len(rows)} 行）")
    if dry or not doomed:
        return 0

    token = client.token()
    for start in range(0, len(doomed), 100):
        chunk = doomed[start : start + 100]
        result = _json_request(
            "POST",
            f"{BASE}/bitable/v1/apps/{fcfg.app_token}/tables/{fcfg.table_id}/records/batch_delete",
            token,
            {"records": chunk},
        )
        print(" 删除" if result.get("code") == 0 else f" 失败 {result.get('code')} {result.get('msg')}",
              len(chunk), "行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
