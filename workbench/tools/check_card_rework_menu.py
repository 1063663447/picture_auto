"""成品卡「改图」三项菜单的回归验证（9.6.25）。

出好的图要改，三条路：直接重新生成 / 对话改图 / 框选改图。
这个脚本不调接口、不花钱，检查的是「接线」有没有断：

  * 成品卡（cardShell）里是三条路，而不是原来那个单一「改图」按钮；
  * 三条路各自连到对的动作：retrySingle / openHarness("rework") / openAnnotator(mode=rework)；
  * 框选改图不经过对话：submitAnnotation 会走 submitCardRework，带 annotation_id 打 /rework；
  * 后端这条链也在：/rework 收 annotation_id → create_rework_job 换算成绝对路径 → runner 附给接口。

用法：python tools/check_card_rework_menu.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SRV = ROOT / "skillbridge"

FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("  ✓ " if ok else "  ✗ ") + label + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAIL.append(label)


def main() -> int:
    app_js = (WEB / "app.js").read_text(encoding="utf-8")
    api_py = (SRV / "api.py").read_text(encoding="utf-8")
    service_py = (SRV / "service.py").read_text(encoding="utf-8")
    runner_py = (SRV / "runner.py").read_text(encoding="utf-8")

    print("1) 成品卡上是三条路")
    check("卡片按钮改成了「改图 ⌄」菜单入口", 'data-rework-menu="${esc(task.id)}"' in app_js)
    check("旧的单一 data-rework 按钮已经从成品卡移除",
          '<button class="btn btn-ghost btn-sm" data-rework="${esc(task.id)}">' not in app_js)
    inside = app_js[app_js.find("function openReworkMenu"):]
    for label in ("直接重新生成", "对话改图", "框选改图"):
        check(f"菜单里有「{label}」", label in inside[:2000])

    print("2) 三条路各自接到对的动作")
    handler = app_js[app_js.find('document.addEventListener("click", (event) => {'):][:2000]
    check("直接重新生成 → retrySingle", 'kind === "regen"' in handler and "retrySingle(taskId)" in handler)
    check("对话改图 → openHarness(\"rework\")", 'kind === "chat"' in handler and 'openHarness("rework", [taskId])' in handler)
    check("框选改图 → openAnnotator(mode=rework)",
          'kind === "annotate"' in handler and 'mode: "rework"' in handler)
    check("没出图的卡片点不开菜单（先判断 status）", 'task.status !== "done"' in handler)

    print("3) 框选改图不经过对话")
    check("标注器记住了 mode", 'anno.mode = ref.mode === "rework" ? "rework" : "master"' in app_js)
    check("提交时按 mode 分流到 submitCardRework",
          'if (anno.mode === "rework")' in app_js and "submitCardRework({" in app_js)
    check("成品卡这条路带 annotation_id 打 /rework",
          'form.append("annotation_id", annotationId)' in app_js
          and "`/api/jobs/${jobId}/rework`" in app_js)
    check("提交后去看新任务（和对话改图一致）", "watchJob(res.job.id)" in app_js)

    print("4) 后端链路")
    route = api_py[api_py.find('@app.post("/api/jobs/{job_id}/rework")'):][:1800]
    check("/rework 路由收 annotation_id", "annotation_id: str = Form(\"\")" in route)
    check("路由把 annotation_id 传给 service", "annotation_id=annotation_id" in route)
    check("create_rework_job 会把 annotation_id 换算成路径",
          "if annotation_id and not annotation_path:" in service_py
          and "annotated = self._annotation_path(source_job, annotation_id)" in service_py)
    check("runner 会把标注图当参考图发给接口", "annotation_path" in runner_py and "annotation_clause" in runner_py)

    print("5) 前端语法")
    for name in ("app.js", "blackglass-ui.js"):
        out = subprocess.run(
            ["node", "--check", str(WEB / name)], capture_output=True, text=True
        )
        check(f"{name} 语法通过", out.returncode == 0, (out.stderr or "")[:120])
    version = re.search(r"app\.js\?v=([\w\.]+)", (WEB / "index.html").read_text(encoding="utf-8"))
    check("index.html 带了缓存号", bool(version), str(version))

    if FAIL:
        print(f"\n失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
