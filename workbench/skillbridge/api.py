"""FastAPI app: workbench UI + JSON API."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from PIL import Image
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, harness, prompts, refine
from .config import WORKBENCH_DIR
from .providers.base import ProviderError
from .service import Workbench, WorkbenchError
from .skill import load_skill

WEB_DIR = WORKBENCH_DIR / "web"


def create_app(config_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.wb = Workbench(config_path)
        try:
            yield
        finally:
            await app.state.wb.shutdown()

    app = FastAPI(title="服装改款换色工作台", version="1.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def access_guard(request: Request, call_next):
        """配了 server.auth_token 就要求口令，避免公网隧道把工作台裸暴露出去。"""
        bench = getattr(app.state, "wb", None)
        if bench is None:
            return await call_next(request)
        token = str((bench.config.get("server") or {}).get("auth_token") or "")
        if not token:
            return await call_next(request)
        provided = (
            request.query_params.get("token")
            or request.headers.get("x-workbench-token")
            or request.cookies.get("wb_token")
        )
        if provided != token:
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>需要口令</title>"
                "<div style='font:14px/1.7 system-ui;max-width:560px;margin:12vh auto;padding:24px'>"
                "<h2 style='margin:0 0 8px'>需要访问口令</h2>"
                "<p style='color:#555'>这个工作台在公网隧道后面，请在地址后加 "
                "<code>?token=你的口令</code> 打开一次，浏览器会记住 Cookie。<br>"
                "口令在 <code>config.json → server.auth_token</code>。"
                "</p></div>",
                status_code=401,
            )
        response = await call_next(request)
        if request.query_params.get("token") == token:
            response.set_cookie(
                "wb_token", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
            )
        return response

    def wb(request: Request) -> Workbench:
        return request.app.state.wb

    @app.exception_handler(WorkbenchError)
    async def _workbench_error(_request: Request, exc: WorkbenchError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.exception_handler(ProviderError)
    async def _provider_error(_request: Request, exc: ProviderError):
        """上游接口出问题时给出可读错误，而不是 500。"""
        return JSONResponse(
            {"ok": False, "error": exc.message, "provider": exc.provider},
            status_code=502,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        return JSONResponse(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )

    # ------------------------------------------------------------- metadata --
    @app.get("/api/health")
    async def health(request: Request):
        bench = wb(request)
        return {
            "ok": True,
            "skill_root": str(bench.skill.root),
            "types": len(bench.skill.types),
            "providers": len(bench.providers),
            "providers_ui": len(bench.provider_payload()),
            "jobs": len(bench.jobs),
        }

    @app.get("/api/skill")
    async def skill_info(request: Request, reload: bool = False):
        bench = wb(request)
        if reload:
            bench.reload_skill()
        return bench.skill_payload()

    @app.get("/api/providers")
    async def providers(request: Request):
        bench = wb(request)
        return {
            "ok": True,
            "providers": bench.provider_payload(),
            "defaults": bench.defaults(),
            "ui": bench.ui_flags(),
        }

    @app.get("/api/models")
    async def list_models(request: Request, provider: str = "", refresh: bool = False):
        bench = wb(request)
        return {"ok": True, **await bench.list_models(provider, refresh=refresh)}

    @app.get("/api/refs/{type_dir}/{filename}")
    async def reference_image(request: Request, type_dir: str, filename: str):
        bench = wb(request)
        path = bench.skill.root / "assets" / "color-references" / Path(type_dir).name / Path(filename).name
        if not path.is_file():
            return JSONResponse({"ok": False, "error": "参考图不存在"}, status_code=404)
        return FileResponse(path)

    @app.post("/api/preview")
    async def preview_prompt(request: Request):
        bench = wb(request)
        body = await request.json()
        from . import prompts

        garment = bench.skill.type_by_alias(body.get("garment_type"))
        color = None
        if body.get("color"):
            color = garment.color(str(body["color"]))
        payload = prompts.preview(bench.skill, garment, color)
        payload["ok"] = True
        return payload

    # ------------------------------------------------------------------ jobs --
    @app.post("/api/jobs")
    async def create_job(
        request: Request,
        files: list[UploadFile] = File(...),
        garment_type: str = Form(...),
        colors: list[str] = Form(default=[]),
        mode: str = Form("single_pass"),
        provider: str = Form(""),
        model: str = Form(""),
        concurrency: str = Form("auto"),
        rpm: int | None = Form(None),
        size: str = Form("auto"),
        quality: str = Form("high"),
        seed: int | None = Form(None),
        max_retries: int | None = Form(None),
        timeout_sec: float | None = Form(None),
        output_format: str = Form("png"),
        send_reference: bool = Form(True),
        input_fidelity: str = Form(""),
        extra_requirements: str = Form(""),
        start: bool = Form(True),
    ):
        bench = wb(request)
        uploads: list[tuple[str, bytes]] = []
        for item in files:
            data = await item.read()
            if not data:
                continue
            uploads.append((item.filename or "upload.png", data))
        parsed_concurrency: int | str | None = concurrency
        if str(concurrency).strip().lower() in {"", "auto", "none"}:
            parsed_concurrency = "auto"
        else:
            try:
                parsed_concurrency = int(concurrency)
            except (TypeError, ValueError):
                parsed_concurrency = "auto"
        job = bench.create_job(
            uploads,
            garment_type=garment_type,
            colors=colors,
            mode=mode,
            provider=provider,
            model=model,
            concurrency=parsed_concurrency,
            rpm=rpm,
            size=size,
            quality=quality,
            seed=seed,
            max_retries=max_retries,
            timeout_sec=timeout_sec,
            output_format=output_format,
            send_reference=send_reference,
            input_fidelity=input_fidelity,
            extra_requirements=extra_requirements,
        )
        if start:
            bench.start(job.id)
        return {"ok": True, "job": job.as_dict(with_tasks=True, with_log=True)}

    @app.get("/api/jobs")
    async def list_jobs(request: Request):
        return {"ok": True, "jobs": wb(request).list_jobs()}

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str):
        job = wb(request).get_job(job_id)
        return {"ok": True, "job": job.as_dict(with_tasks=True, with_log=True)}

    @app.post("/api/jobs/{job_id}/start")
    async def start_job(request: Request, job_id: str):
        return {"ok": True, "job": wb(request).start(job_id)}

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(request: Request, job_id: str, task_ids: list[str] = Form(default=[])):
        return {"ok": True, "job": wb(request).retry(job_id, task_ids)}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(request: Request, job_id: str):
        return {"ok": True, "job": wb(request).cancel(job_id)}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(request: Request, job_id: str):
        wb(request).delete(job_id)
        return {"ok": True}

    # ------------------------------------------------- pricing / usage ------
    @app.get("/api/pricing")
    async def pricing(request: Request):
        return {"ok": True, **wb(request).pricing_payload()}

    @app.get("/api/usage")
    async def usage(request: Request):
        return {"ok": True, **wb(request).usage_payload()}

    @app.post("/api/drafts")
    async def create_draft(request: Request, files: list[UploadFile] = File(...)):
        bench = wb(request)
        uploads: list[tuple[str, bytes]] = []
        for item in files:
            data = await item.read()
            if data:
                uploads.append((item.filename or "upload.png", data))
        return {"ok": True, **bench.save_draft(uploads)}

    # ----------------------------------------------------------- harness --
    @app.post("/api/harness/stream")
    async def harness_stream(request: Request):
        """对话框 + 流式输出：DeepSeek agent（可看图、可查价、可直接开工）优先，失败降级本地分析师。"""
        bench = wb(request)
        body = await request.json()
        mode = str(body.get("mode") or "intake")
        instruction = str(body.get("instruction") or "")
        job_id = str(body.get("job_id") or "")
        task_ids = [str(t) for t in (body.get("task_ids") or [])]
        draft_id = str(body.get("draft_id") or "")
        count = int(body.get("count") or 0)
        base_prompt = str(body.get("base_prompt") or "")
        garment_slug = body.get("garment_type")
        colors = [str(c) for c in (body.get("colors") or []) if str(c).strip()]
        model_choice = str(body.get("model") or "")

        garment = None
        color = ""
        image_paths: list[Path] = []
        if job_id:
            job = bench.get_job(job_id)
            garment = bench.skill.type_by_alias(job.params.get("garment_type"))
            ready = [t for t in job.tasks if t.out and (not task_ids or t.id in task_ids)]
            count = count or len(ready)
            if ready:
                base_prompt = base_prompt or ready[0].prompt
                color = ready[0].color_name
                image_paths = [(job.root or bench.job_dir(job_id)) / str(t.out) for t in ready][:3]
            model_choice = model_choice or str(job.params.get("model") or "")
        else:
            try:
                garment = bench.skill.type_by_alias(garment_slug)
            except KeyError:
                garment = bench.skill.type_by_alias(next(iter(bench.skill.types)))
            if colors:
                ref = garment.color(colors[0])
            else:
                ref = garment.colors[0] if garment.colors else None
            color = ref.name_zh if ref else ""
            if draft_id:
                image_paths = bench.draft_image_paths(draft_id)[:3]
            if not base_prompt and ref is not None:
                base_prompt = prompts.build_single_pass(
                    bench.skill,
                    garment,
                    ref,
                    " ".join(instruction.split()) if instruction else None,
                )

        defaults = bench.defaults()
        agent_cfg = defaults.get("harness") or {}
        agent_key = str(bench.env.get(str(agent_cfg.get("api_key_env") or ""), "")).strip()
        agent_model = str(agent_cfg.get("model") or "").strip()
        provider_obj = bench.get_provider(None)
        model_choice = model_choice or str(provider_obj.cfg.get("model") or "")

        def sse(event: dict) -> bytes:
            payload = json.dumps(event["data"], ensure_ascii=False)
            return f"event: {event['event']}\ndata: {payload}\n\n".encode("utf-8")

        async def local_frames(reason: str = ""):
            if reason:
                yield sse({"event": "delta", "data": {"text": f"（{reason}，改用本地分析）\n\n"}})
            async for event in harness.stream(
                mode=mode,
                instruction=instruction,
                base_prompt=base_prompt,
                garment=garment.display_name if garment else "",
                color=color,
                count=count,
                llm_cfg={},
                env=bench.env,
            ):
                yield sse(event)

        created = {"job_id": "", "requirements": ""}

        async def agent_frames(config: dict):
            ctx = agent.AgentContext(
                mode=mode,
                garment=garment.display_name if garment else "",
                color=color,
                count=count,
                model=model_choice,
                base_prompt=base_prompt,
                draft_id=draft_id,
                job_id=job_id,
                task_ids=task_ids,
            )

            async def tool_list_models() -> str:
                data = await bench.list_models(None)
                rows = [m for m in data["models"] if m.get("image")]
                lines = [
                    f"{m['id']} · {m.get('price_label') or '未定价'}"
                    for m in rows
                ]
                return f"当前 {len(rows)} 个图片模型：\n" + "\n".join(lines[:30])

            async def tool_inspect() -> str:
                from .providers.local_recolor import LocalRecolorProvider, _border_color

                helper = LocalRecolorProvider("inspect", {})
                lines = []
                targets = image_paths[:3]
                if not targets:
                    return "没有可检查的图片（还没上传或还没生成成品）"
                for path in targets:
                    if not path.is_file():
                        continue
                    try:
                        with Image.open(path) as handle:
                            width, height = handle.size
                            rgb = handle.convert("RGB")
                        background = _border_color(rgb)
                        garment_tone = helper._estimate_garment(rgb, background)
                        ratio = round(width / max(1, height), 3)
                        line = f"{path.name}：{width}×{height}（横:竖 ≈ {ratio}）面料主色 RGB{garment_tone}"
                        if max(width, height) < 1200:
                            line += " · 分辨率偏低"
                        lines.append(line)
                    except Exception:  # noqa: BLE001
                        lines.append(f"{path.name}：读取失败")
                lines.append("提示：长边 < 1200px 的原图会被模型放大补细节，图案字形容易漂。")
                return "\n".join(lines)

            async def tool_estimate(model: str, amount: int) -> str:
                est = bench.pricer.estimate(
                    model or model_choice,
                    amount or count,
                    str(body.get("size") or "auto"),
                )
                if not est["priced"]:
                    return f"{est['model']} 未定价（可在 pricing.json 里填单价）"
                tier = f" · {est['tier']} 档" if est.get("tier") else ""
                return (
                    f"{est['model']} × {est['count']} 张 = {est['total']} {est['currency']}"
                    f"（单价 {est['unit']} {est['currency']}/张{tier}）"
                )

            async def tool_submit(extra: str) -> str:
                if not draft_id:
                    return "失败：没有待生成的图片草稿"
                uploads = bench.draft_uploads(draft_id)
                job = bench.create_job(
                    uploads,
                    garment_type=garment.slug if garment else None,
                    colors=colors or None,
                    provider=provider_obj.name,
                    model=model_choice,
                    concurrency="auto",
                    size=str(body.get("size") or "auto"),
                    quality=str(body.get("quality") or "high"),
                    output_format=str(body.get("output_format") or "png"),
                    extra_requirements=extra,
                )
                bench.start(job.id)
                created["job_id"] = job.id
                created["requirements"] = " ".join((extra or "").split())
                est = job.params.get("estimate") or {}
                cost = (
                    f"预估 {est.get('total')} {est.get('currency')}"
                    if est.get("priced")
                    else "该模型未定价"
                )
                return (
                    f"成功：任务 {job.id} 已开工，{len(job.tasks)} 张，并发 {job.params['concurrency']}，{cost}"
                )

            async def tool_rework(text: str) -> str:
                if not job_id or not task_ids:
                    return "失败：没有选中的成品"
                job = await bench.create_rework_job(
                    job_id, task_ids, text or instruction or "按需求调整",
                    provider=provider_obj.name, model=model_choice,
                )
                bench.start(job.id)
                created["job_id"] = job.id
                created["requirements"] = " ".join((text or "").split())
                return f"成功：返工任务 {job.id} 已提交，{len(job.tasks)} 张"

            async def tool_status(target: str) -> str:
                which = target or job_id
                if not which:
                    return "当前没有任务"
                job = bench.get_job(which)
                stats = job.counts()
                return (
                    f"{job.id} · {job.status} · {job.stage} · "
                    f"完成 {stats['done']}/{stats['total']} 失败 {stats['failed']}"
                )

            ctx.list_models = tool_list_models
            ctx.inspect = tool_inspect
            ctx.estimate = tool_estimate
            ctx.submit_generation = tool_submit
            ctx.submit_rework = tool_rework
            ctx.job_status = tool_status
            for path in image_paths:
                data_url = agent.encode_image(path)
                if data_url:
                    ctx.images.append(data_url)

            if created["job_id"]:
                pass
            async for event in agent.run_agent(
                user_message=instruction,
                ctx=ctx,
                base_url=str(config.get("base_url") or ""),
                model=str(config.get("model") or ""),
                api_key=agent_key,
                max_steps=int(config.get("max_steps") or 6),
            ):
                if event["event"] == "prompt":
                    data = dict(event["data"])
                    data["requirements"] = (
                        created["requirements"] or data.get("requirements") or ""
                    )
                    data["instruction"] = " ".join(instruction.split())
                    if created["job_id"]:
                        data["job_id"] = created["job_id"]
                    event = {"event": "prompt", "data": data}
                yield sse(event)
            if created["job_id"]:
                yield sse({"event": "job", "data": {"job_id": created["job_id"]}})

        async def frames():
            use_agent = bool(agent_cfg.get("enabled")) and bool(agent_key) and bool(agent_model)
            if not use_agent:
                async for chunk in local_frames("未配置在线模型"):
                    yield chunk
                return
            try:
                async for chunk in agent_frames(agent_cfg):
                    yield chunk
            except Exception as exc:  # noqa: BLE001 - 永远给用户一个可用结果
                async for chunk in local_frames(f"在线模型不可用：{type(exc).__name__}"):
                    yield chunk

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---------------------------------------------------------------- 改图 --
    @app.post("/api/rework/preview")
    async def rework_preview(request: Request):
        """返回将要提交的改图提示词与交接说明（供 harness / 人工核对）。"""
        bench = wb(request)
        body = await request.json()
        job = bench.get_job(str(body.get("job_id") or ""))
        wanted = {str(t) for t in (body.get("task_ids") or [])}
        instruction = str(body.get("instruction") or "").strip()
        ready = [t for t in job.tasks if t.out]
        selected = [t for t in ready if t.id in wanted] or ready[:1]
        if not selected:
            return JSONResponse({"ok": False, "error": "没有可用的成品图片"}, status_code=400)
        task = selected[0]
        prompt, refiner = await refine.compose(
            instruction,
            task.prompt,
            refiner=str(body.get("refiner") or "template"),
            cfg=(bench.defaults().get("rework") or {}).get("llm") or {},
            env=bench.env,
        )
        brief = refine.format_brief(
            instruction=instruction,
            original_prompt=task.prompt,
            reference_image=str(task.out),
            task_id=task.id,
            garment=str(job.params.get("garment_name") or ""),
            color=task.color_name,
        )
        return {
            "ok": True,
            "count": len(selected),
            "refiner": refiner,
            "prompt": prompt,
            "original_prompt": task.prompt,
            "brief": brief,
        }

    @app.post("/api/jobs/{job_id}/rework")
    async def rework_job(
        request: Request,
        job_id: str,
        task_ids: list[str] = Form(default=[]),
        instruction: str = Form(""),
        provider: str = Form(""),
        model: str = Form(""),
        size: str = Form(""),
        quality: str = Form(""),
        output_format: str = Form(""),
        timeout_sec: float | None = Form(None),
        refiner: str = Form(""),
        prompt_override: str = Form(""),
        start: bool = Form(True),
    ):
        bench = wb(request)
        job = await bench.create_rework_job(
            job_id,
            task_ids,
            instruction,
            provider=provider or None,
            model=model or None,
            size=size or None,
            quality=quality or None,
            output_format=output_format or None,
            timeout_sec=timeout_sec,
            refiner=refiner or None,
            prompt_override=prompt_override,
        )
        if start:
            bench.start(job.id)
        return {"ok": True, "job": job.as_dict(with_tasks=True, with_log=True)}

    @app.get("/api/jobs/{job_id}/export")
    async def export_selected(request: Request, job_id: str, tasks: str = ""):
        bench = wb(request)
        ids = [part.strip() for part in tasks.split(",") if part.strip()]
        path = bench.export_zip(job_id, ids)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    # ---------------------------------------------------------------- files --
    @app.get("/api/jobs/{job_id}/image/{task_id}")
    async def job_image(request: Request, job_id: str, task_id: str, download: bool = False):
        path = wb(request).image_path(job_id, task_id)
        if download:
            return FileResponse(path, filename=path.name)
        return FileResponse(path)

    @app.get("/api/jobs/{job_id}/download")
    async def download(request: Request, job_id: str):
        path = wb(request).zip_path(job_id)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


__all__ = ["create_app", "load_skill"]
