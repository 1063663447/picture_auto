"""从上一版便携包（9.4.1）里把构建资产（embed Python + 20 个 wheel + 镜像 URL 缓存）取出来，
生成 build_internal_portable.py 需要的 --assets 目录，这样重建时**不用联网**再找一遍 wheel。

产物（scratch/assets-9.4.4/）：
  python-3.12.10-embed-amd64.zip
  wheels/*.whl
  assets.json        （builder 要的 filename + sha256 清单）
  mirror-urls.json   （builder 会写入；先按上一版填好，避免联网）
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "releases" / "PictureWorkbench1107.zip"   # 上一版（9.4.1）的包
DEFAULT_OUT = ROOT / ".." / "scratch" / "assets-portable"
SRC = DEFAULT_SRC
OUT = DEFAULT_OUT


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    global SRC, OUT
    for arg in sys.argv[1:]:
        if arg.startswith("--from="):
            SRC = Path(arg.split("=", 1)[1]).resolve()
        elif arg.startswith("--out="):
            OUT = Path(arg.split("=", 1)[1]).resolve()
    if not SRC.is_file():
        print("找不到上一版包：", SRC)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "wheels").mkdir(exist_ok=True)
    with zipfile.ZipFile(SRC) as archive:
        manifest = json.loads(archive.read("PictureWorkbench-9.4.1/package-manifest.json"))
        python = manifest["python_archive"]
        # 1) embed Python
        inner = f"PictureWorkbench-9.4.1/offline-cache/{python['filename']}"
        (OUT / python["filename"]).write_bytes(archive.read(inner))
        # 2) wheels
        for wheel in manifest["wheels"]:
            name = Path(wheel["filename"]).name
            data = archive.read(f"PictureWorkbench-9.4.1/offline-cache/wheels/{name}")
            (OUT / "wheels" / name).write_bytes(data)
        specs = {
            "python": {"filename": python["filename"], "sha256": sha(OUT / python["filename"])},
            "wheels": [
                {"filename": Path(w["filename"]).name,
                 "sha256": sha(OUT / "wheels" / Path(w["filename"]).name)}
                for w in manifest["wheels"]
            ],
        }
        mirrors = {Path(w["filename"]).name: w.get("urls") or [] for w in manifest["wheels"]}
        mirrors[python["filename"]] = python.get("urls") or []
    (OUT / "assets.json").write_text(json.dumps(specs, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "mirror-urls.json").write_text(json.dumps(mirrors, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"资产已就位：{OUT}")
    print(f"  python: {specs['python']['filename']}  {specs['python']['sha256'][:16]}…")
    print(f"  wheels: {len(specs['wheels'])} 个")
    print(f"  合计 {total/1024/1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
