"""保留模特换装的回归（9.6.35）。

客户：分别放入模特图和图案图后，保持模特和姿势不变，仅更换衣服图案和材质。

离线验证：
  1. prompts.build_model_swap 里有「人和姿势锁 + 只换图案/材质 + 版型不动」；
  2. service.create_model_swap_job：一张模特图一条任务、图案图落盘记进 params、
     不进审核闸门、不排派生色；
  3. runner 有 model_swap 分支，_execute_model_swap 发两张图且关掉 input_fidelity；
  4. API 路由按 mode=model_swap 分流（不受「必须选款式」限制）；
  5. 前端有勾选项 + 图案图入口 + createJob 里走 model_swap。

用法：python tools/check_model_swap.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from skillbridge import prompts  # noqa: E402
from skillbridge.config import load_config, load_env  # noqa: E402
from skillbridge.service import Workbench  # noqa: E402

FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def png_bytes(color: tuple[int, int, int] = (240, 240, 240)) -> bytes:
    image = Image.new("RGB", (256, 256), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    print("1) 提示词：人和姿势锁 + 只换图案/材质")
    text = prompts.build_model_swap("图案稍微放大一点")
    for mark in (
        "MODEL AND POSE LOCK",
        "same pose",
        "same background",
        "GARMENT MATERIAL AND PRINT SWAP",
        "PRINT:",
        "FABRIC:",
        "KEEP THE CUT",
        "OPERATOR NOTE",
    ):
        check(f"含「{mark}」", mark in text)
    for mark in ("Do not change the model's face", "Do not change the garment's cut",
                 "do not invent any print"):
        check(f"负面含「{mark}」", mark in prompts.MODEL_SWAP_NEGATIVE)

    print("2) service.create_model_swap_job")
    temp = Path(tempfile.mkdtemp(prefix="wb-swap-"))
    try:
        env = load_env()
        env["WORKBENCH_DATA_DIR"] = str(temp)   # 只换数据目录，其余用真实配置
        bench = Workbench()
        bench.data_dir = temp
        bench.jobs_dir = temp / "jobs"
        bench.jobs_dir.mkdir(parents=True, exist_ok=True)
        bench.jobs = {}
        bench._tasks = {}
        bench._runners = {}

        job = bench.create_model_swap_job(
            [("model-a.png", png_bytes()), ("model-b.png", png_bytes((200, 30, 30)))],
            ("pattern.jpg", png_bytes((20, 20, 20))),
            garment_type="06-hoodie",
            provider="mock",
        )
        check("两张模特图 → 两条任务", len(job.tasks) == 2, str(len(job.tasks)))
        check("kind/mode = model_swap",
              job.params.get("kind") == "model_swap" and job.params.get("mode") == "model_swap")
        check("图案图落盘并记路径",
              bool(job.params.get("pattern_image"))
              and (job.root / job.params["pattern_image"]).is_file(),
              str(job.params.get("pattern_image")))
        check("不进审核闸门", job.params.get("review_gate") is False)
        check("不排派生色（shape_lock off）", job.params.get("shape_lock") == "off")
        check("主体按真人处理", job.params.get("subject") == "person")
        check("任务带模特图 source 且是 master",
              all(t.source and t.master for t in job.tasks))
        check("颜色占位是「换装」", all(t.color == "pattern" and t.color_name == "换装"
                                    for t in job.tasks))
        check("参数里有换装说明（日志）",
              any("保留模特换装" in str(row.get("msg")) for row in job.log))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    print("3) runner / API / 前端接线")
    runner_src = (ROOT / "skillbridge" / "runner.py").read_text(encoding="utf-8")
    api_src = (ROOT / "skillbridge" / "api.py").read_text(encoding="utf-8")
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    check("runner 有 model_swap 分支", 'self.mode == "model_swap"' in runner_src)
    check("runner 发两张图并关掉 input_fidelity",
          "images = [model_path, pattern_path]" in runner_src
          and "input_fidelity=None" in runner_src)
    check("接口只收 1 张图时明确报错",
          "换装需要模特图 + 图案图两张" in runner_src)
    check("API 收 pattern 文件", "pattern: list[UploadFile] = File(default=[])" in api_src)
    check("API 在「必须选款式」之前分流",
          api_src.find('== "model_swap"') < api_src.find("请至少选择一个款式"))
    check("前端有「保留模特换装」勾选", 'id="swapToggle"' in html)
    check("前端有图案图入口", 'id="swapPatternInput"' in html and 'id="swapPatternBtn"' in html)
    check("createJob 里走 model_swap", 'form.append("mode", "model_swap")' in app_js)
    check("没选图案图时按钮拦住", "请再选一张图案/面料图" in app_js)

    print("4) 9.6.37 三处修复")
    css = (ROOT / "web" / "review-cards.css").read_text(encoding="utf-8")
    service_src = (ROOT / "skillbridge" / "service.py").read_text(encoding="utf-8")
    check("图案图区是 dropzone（可拖入）", 'id="swapDrop"' in html and "swapDrop.addEventListener(\"drop\"" in app_js)
    check("图案图有缩略图预览", 'id="swapThumbs"' in html and "function renderSwapThumb(" in app_js)
    check("图案图支持粘贴（还没选时第一张给图案）",
          "swapReady() && !state.swapPattern" in app_js and "setSwapPattern(files.shift())" in app_js)
    check("图案图校验类型/大小", "图案图请用 PNG" in app_js and "图案图不能超过 20 MB" in app_js)
    check("换装分支把当前款式传上去", 'form.append("garment_type", (primaryStyle().slug' in app_js)
    check("后端默认款式不再写死弹力短袖",
          'self._resolve_garment(garment_type or "10-stretch-tee")' not in service_src
          and "first = next(iter(self.skill.types.values()), None)" in service_src)
    check("左栏允许纵向滚动", ".flow-shell .controls { overflow-y: auto" in css)
    check("换装块紧凑（一行放缩略图+按钮）", ".swap-pattern .swap-drop" in css)

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过：模特图 + 图案图 → 人和姿势不动，只换图案与材质")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
