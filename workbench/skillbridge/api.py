"""FastAPI app: workbench UI + JSON API."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path

from PIL import Image
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import agent, auth, feishu, feishu_export, harness, prompts, refine
from . import subject as subject_mod
from .harness_media import HarnessImage, inspect_images, rework_images
from .harness_context import batch_context, batch_prompt, job_selections, target_requirements, validate_selections
from .config import WORKBENCH_DIR
from .providers.base import ProviderError
from .service import Workbench, WorkbenchError
from .skill import load_skill

WEB_DIR = WORKBENCH_DIR / "web"


def create_app(config_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 闸门每张图要读/写飞书（走线程池）。默认线程池只有 cpu+4 个线程，
        # 多任务并发时会排队等线程，看起来像「凭空等了几十秒」。这里放大到 64。
        try:
            from concurrent.futures import ThreadPoolExecutor

            asyncio.get_running_loop().set_default_executor(
                ThreadPoolExecutor(max_workers=64, thread_name_prefix="wb-io")
            )
        except Exception:  # noqa: BLE001
            pass
        app.state.wb = Workbench(config_path)
        # 上次没写进飞书的任务，启动后补一遍（在线程里跑，别拖慢启动）
        try:
            asyncio.get_running_loop().run_in_executor(None, app.state.wb.sync_pending)
        except Exception:  # noqa: BLE001
            pass
        try:
            yield
        finally:
            await app.state.wb.shutdown()

    app = FastAPI(title="服装改款换色工作台", version="1.1.0", lifespan=lifespan)
    # 文本响应压缩（JSON/HTML/JS/CSS），图片本来就不能再压，走 Middleware 只压 text
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def access_guard(request: Request, call_next):
        """账号登录 + 口令兜底：登录前只放行登录页与静态资源。"""
        bench = getattr(app.state, "wb", None)
        if bench is None:
            return await call_next(request)
        path = request.url.path
        # 健康检查端点必须免口令，否则云平台（Render/Koyeb/Cloud Run）判定服务不健康
        if path in {"/api/health", "/healthz"}:
            return await call_next(request)

        server = bench.config.get("server") or {}
        token = str(server.get("auth_token") or "")
        secret = session_secret(bench)
        ttl = int(server.get("session_ttl") or auth.SESSION_TTL)
        login_required = bool(server.get("login_required", True))

        # 1) 机器口令（?token= / x-workbench-token / 旧 Cookie）永远有效，防止把自己锁在门外
        provided = (
            request.query_params.get("token")
            or request.headers.get("x-workbench-token")
            or request.cookies.get("wb_token")
        )
        if token and provided == token:
            request.state.account = "token"
            request.state.role = "admin"
            request.state.display = "口令访问"
            response = await call_next(request)
            if request.query_params.get("token") == token:
                response.set_cookie(
                    "wb_token", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
                )
            return response

        # 2) 会话 Cookie
        username = auth.read_session(request.cookies.get("wb_session"), secret)
        account = None
        if username:
            try:
                account = (await asyncio.to_thread(bench.accounts.load)).get(username.lower())
            except Exception:  # noqa: BLE001 - 账号表读不到时不阻断已登录会话
                account = None
            if account is not None and not account.active:
                account = None
        if account is not None:
            request.state.account = account.username
            request.state.role = "admin" if account.is_admin else "member"
            request.state.display = account.display or account.username
            return await call_next(request)

        request.state.account = ""
        request.state.role = ""
        if not login_required or not token:
            return await call_next(request)

        # 3) 未登录：页面给登录表单，接口返回 401，静态资源照常放行
        if path.startswith("/api/") or path == "/api":
            return JSONResponse(
                {"ok": False, "error": "未登录或会话已过期", "login_required": True},
                status_code=401,
            )
        if path in {"/", "/index.html"}:
            return HTMLResponse(auth.LOGIN_PAGE.replace("__ERROR__", ""))
        return await call_next(request)

    def current_account(request: Request) -> str:
        return str(getattr(request.state, "account", "") or "")

    def current_role(request: Request) -> str:
        return str(getattr(request.state, "role", "") or "")

    def session_secret(bench: Workbench) -> str:
        """会话签名密钥：优先环境变量（部署时别写进仓库），再回退配置/口令。"""
        server = bench.config.get("server") or {}
        return (
            os.environ.get("WORKBENCH_SESSION_SECRET")
            or str(server.get("session_secret") or "")
            or str(server.get("auth_token") or "")
            or "workbench-session"
        )

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

    # ---------------------------------------------------------------- 登录 --
    def _safe_next(value: str) -> str:
        value = (value or "").strip()
        return value if value.startswith("/") and not value.startswith("//") else "/"

    @app.get("/login")
    async def login_page(request: Request):
        bench = wb(request)
        secret = session_secret(bench)
        nxt = _safe_next(request.query_params.get("next") or "")
        if auth.read_session(request.cookies.get("wb_session"), secret):
            return RedirectResponse(nxt, status_code=302)
        error = request.query_params.get("error")
        notice = '<p class="err">账号或密码不对，或账号已停用</p>' if error else ""
        return HTMLResponse(
            auth.LOGIN_PAGE.replace("__ERROR__", notice).replace("__NEXT__", nxt)
        )

    @app.post("/login")
    async def login_submit(request: Request):
        bench = wb(request)
        form = await request.form()
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")
        nxt = _safe_next(str(form.get("next") or ""))
        account = await asyncio.to_thread(bench.accounts.authenticate, username, password)
        if account is None:
            return RedirectResponse(
                f"/login?error=1&next={urllib.parse.quote(nxt, safe='')}", status_code=302
            )
        server = bench.config.get("server") or {}
        secret = session_secret(bench)
        ttl = int(server.get("session_ttl") or auth.SESSION_TTL)
        response = RedirectResponse(nxt, status_code=302)
        response.set_cookie(
            "wb_session",
            auth.make_session(account.username, secret, ttl),
            httponly=True,
            samesite="lax",
            max_age=ttl,
        )
        return response

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("wb_session")
        response.delete_cookie("wb_token")
        return response

    @app.get("/api/session")
    async def session_info(request: Request):
        bench = wb(request)
        name = current_account(request)
        if not name:
            return {"ok": True, "authenticated": False}
        accounts = await asyncio.to_thread(bench.accounts.load)
        account = accounts.get(name.lower())
        return {
            "ok": True,
            "authenticated": True,
            "account": name,
            "display": (account.display if account and account.display else
                        getattr(request.state, "display", name)),
            "role": current_role(request),
            "admin": current_role(request) == "admin",
            "source": bench.accounts.source,
        }

    @app.get("/api/accounts")
    async def accounts_list(request: Request):
        bench = wb(request)
        if current_role(request) != "admin":
            return JSONResponse({"ok": False, "error": "只有管理员能看账号用量"}, status_code=403)
        return await asyncio.to_thread(bench.accounts_payload)

    # ------------------------------------------------------------- 管理后台 --
    def _admin_only(request: Request) -> Workbench:
        if current_role(request) != "admin":
            raise PermissionError("只有管理员能进管理后台")
        return wb(request)

    @app.exception_handler(PermissionError)
    async def _permission_error(_request: Request, exc: PermissionError):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=403)

    @app.get("/admin")
    async def admin_page(request: Request):
        if current_role(request) != "admin":
            if current_account(request):  # 已登录但不是管理员
                return RedirectResponse("/?denied=admin", status_code=302)
            return RedirectResponse("/login?next=%2Fadmin", status_code=302)
        path = WEB_DIR / "admin.html"
        if not path.is_file():
            return HTMLResponse("<h1>admin.html 缺失</h1>", status_code=500)
        return FileResponse(path)

    @app.get("/api/admin/overview")
    async def admin_overview(request: Request):
        bench = _admin_only(request)
        return {"ok": True, **await asyncio.to_thread(bench.admin_overview)}

    @app.post("/api/accounts")
    async def account_create(request: Request):
        bench = _admin_only(request)
        body = await request.json()
        try:
            account = await asyncio.to_thread(
                bench.accounts.create,
                str(body.get("username") or ""),
                str(body.get("password") or ""),
                display=str(body.get("display") or ""),
                role=str(body.get("role") or "member"),
                status=str(body.get("status") or "启用"),
                quota=float(body.get("quota") or 0),
                note=str(body.get("note") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True, "account": account.as_dict()}

    @app.patch("/api/accounts/{username}")
    async def account_update(request: Request, username: str):
        bench = _admin_only(request)
        body = await request.json()
        if str(username).lower() == current_account(request).lower() and (
            str(body.get("role") or "").lower() not in {"", "admin"}
            or str(body.get("status") or "") in {"停用", "disabled"}
        ):
            return JSONResponse(
                {"ok": False, "error": "不能把自己降级或停用，请用另一个管理员账号操作"},
                status_code=400,
            )
        try:
            account = await asyncio.to_thread(bench.accounts.update, username, body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True, "account": account.as_dict()}

    @app.post("/api/accounts/{username}/password")
    async def account_password(request: Request, username: str):
        bench = _admin_only(request)
        body = await request.json()
        try:
            await asyncio.to_thread(
                bench.accounts.set_password, username, str(body.get("password") or "")
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True}

    @app.delete("/api/accounts/{username}")
    async def account_delete(request: Request, username: str):
        bench = _admin_only(request)
        if str(username).lower() == current_account(request).lower():
            return JSONResponse({"ok": False, "error": "不能删除自己"}, status_code=400)
        try:
            await asyncio.to_thread(bench.accounts.delete, username)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return {"ok": True}

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
            "gate": bench.gate_public(),
        }

    @app.get("/api/gate")
    async def gate_status(request: Request):
        """全局并发阀门状态（Cloudflare Worker）。未启用时 enabled=false。"""
        bench = wb(request)
        return {"ok": True, **await bench.gate_status()}

    @app.get("/api/models")
    async def list_models(request: Request, provider: str = "", refresh: bool = False):
        bench = wb(request)
        return {"ok": True, **await bench.list_models(provider, refresh=refresh)}

    @app.get("/api/refs/{type_dir}/{filename}")
    async def reference_image(request: Request, type_dir: str, filename: str):
        bench = wb(request)
        safe_dir = Path(type_dir).name
        safe_file = Path(filename).name
        candidates = [
            # 原 skill 自带的色卡
            bench.skill.root / "assets" / "color-references" / safe_dir / safe_file,
            # 工作台自建款式库（library/refs/<slug>/<label>.jpg）
            WORKBENCH_DIR / "library" / "refs" / safe_dir / safe_file,
            # 款式表导入的效果图（library/effects/<slug>/<色号>.jpg）
            WORKBENCH_DIR / "library" / "effects" / safe_dir / safe_file,
        ]
        library_dir = (bench.config.get("library") or {}).get("dir")
        if library_dir:
            base = Path(str(library_dir))
            base = base if base.is_absolute() else WORKBENCH_DIR / base
            candidates.append(base / "refs" / safe_dir / safe_file)
        for path in candidates:
            if path.is_file():
                return FileResponse(path)
        return JSONResponse({"ok": False, "error": "参考图不存在"}, status_code=404)

    @app.get("/api/effects/{slug}/{filename}")
    async def effect_image(request: Request, slug: str, filename: str):
        """效果图（款式表导入的官方平铺产品图）：library/effects/<slug>/<色号>.jpg"""
        bench = wb(request)
        safe_dir = Path(slug).name
        safe_file = Path(filename).name
        library_dir = (bench.config.get("library") or {}).get("dir")
        bases = [WORKBENCH_DIR / "library"]
        if library_dir:
            base = Path(str(library_dir))
            bases.insert(0, base if base.is_absolute() else WORKBENCH_DIR / base)
        for base in bases:
            path = base / "effects" / safe_dir / safe_file
            if path.is_file():
                return FileResponse(path)
        return JSONResponse({"ok": False, "error": "效果图不存在"}, status_code=404)

    @app.post("/api/preview")
    async def preview_prompt(request: Request):
        bench = wb(request)
        body = await request.json()
        from . import prompts

        garment = bench.skill.type_by_alias(body.get("garment_type"))
        color = None
        if body.get("color"):
            color = garment.color(str(body["color"]))
        payload = prompts.preview(
            bench.skill,
            garment,
            color,
            requirement=str(body.get("requirement") or body.get("extra_requirements") or ""),
            pattern_mode=str(body.get("pattern_mode") or ""),
            subject=str(body.get("subject") or ""),
        )
        payload["ok"] = True
        return payload

    # ------------------------------------------------------------------ jobs --
    @app.post("/api/jobs")
    async def create_job(
        request: Request,
        files: list[UploadFile] = File(...),
        garment_type: str = Form(""),
        colors: list[str] = Form(default=[]),
        selections: str = Form(""),
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
        pattern_mode: str = Form(""),
        pattern_brief: str = Form(""),
        subject: str = Form(""),
        retry_on_person: bool = Form(False),
        shape_lock: str = Form(""),
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
        parsed_selections: list[dict] = []
        if str(selections or "").strip():
            try:
                raw = json.loads(selections)
                if isinstance(raw, list):
                    parsed_selections = [item for item in raw if isinstance(item, dict)]
            except json.JSONDecodeError as exc:
                return JSONResponse(
                    {"ok": False, "error": f"selections 不是合法 JSON：{exc}"}, status_code=400
                )
        if not parsed_selections and not garment_type:
            return JSONResponse(
                {"ok": False, "error": "请至少选择一个款式"}, status_code=400
            )
        job = await asyncio.to_thread(
            bench.create_job,
            uploads,
            garment_type=garment_type or None,
            colors=colors,
            selections=parsed_selections or None,
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
            pattern_mode=pattern_mode,
            pattern_brief=pattern_brief,
            subject=subject,
            retry_on_person=retry_on_person,
            shape_lock=shape_lock,
            account=current_account(request),
        )
        if start:
            bench.start(job.id)
        return {"ok": True, "job": job.as_dict(with_tasks=True, with_log=True)}

    @app.get("/api/jobs")
    async def list_jobs(request: Request):
        bench = wb(request)
        jobs = bench.list_jobs()
        # 列表也用 ETag：内容没变就 304，轮询历史几乎不花流量
        etag = 'W/"jobs-%d-%d"' % (len(jobs), int(sum(j.get("updated_at") or 0 for j in jobs)))
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return JSONResponse(
            {"ok": True, "jobs": jobs},
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str, prompts: bool = False):
        job = wb(request).get_job(job_id)
        payload = job.as_dict(with_tasks=True, with_log=True, with_prompts=prompts)
        # 轮询省流量：任务没变化时回 304（浏览器只收几百字节响应头，不收正文）
        stats = payload["stats"]
        signature = (
            f"{job_id}-{int(job.updated_at * 1000)}-{stats['done']}-{stats['failed']}"
            f"-{stats['running']}-{stats['pending']}-{int(prompts)}"
        )
        etag = f'W/"{signature}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        # 默认不带提示词（每条 3~4KB，轮询时纯浪费流量）；需要时加 ?prompts=true
        return JSONResponse(
            {"ok": True, "job": payload},
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    @app.post("/api/jobs/{job_id}/start")
    async def start_job(request: Request, job_id: str):
        return {"ok": True, "job": wb(request).start(job_id)}

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(
        request: Request,
        job_id: str,
        task_ids: list[str] = Form(default=[]),
        only: str = Form(""),
    ):
        return {"ok": True, "job": wb(request).retry(job_id, task_ids, only=only)}

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
        bench = wb(request)
        # 会去飞书读定价（同步 HTTP），必须丢线程池，否则出图轮询/SSE 会被卡住
        account = None if current_role(request) == "admin" else current_account(request)
        return {"ok": True, **await asyncio.to_thread(bench.pricing_payload, account=account)}

    @app.get("/api/usage")
    async def usage(request: Request):
        bench = wb(request)
        return {
            "ok": True,
            **await asyncio.to_thread(bench.usage_payload, account=current_account(request)),
        }

    @app.post("/api/drafts")
    async def create_draft(request: Request, files: list[UploadFile] = File(...)):
        bench = wb(request)
        uploads: list[tuple[str, bytes]] = []
        for item in files:
            data = await item.read()
            if data:
                uploads.append((item.filename or "upload.png", data))
        return {"ok": True, **(await asyncio.to_thread(bench.save_draft, uploads))}

    # ----------------------------------------------------------- harness --
    @app.post("/api/harness/stream")
    async def harness_stream(request: Request):
        """对话框 + 流式输出：DeepSeek agent（可看图、可查价、可直接开工）优先，失败降级本地分析师。"""
        bench = wb(request)
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "请求必须是对象"}, status_code=400)
        mode = str(body.get("mode") or "intake")
        instruction = str(body.get("instruction") or "")
        job_id = str(body.get("job_id") or "")
        draft_id = str(body.get("draft_id") or "")
        if mode not in {"intake", "rework"} or (mode == "rework" and not job_id):
            return JSONResponse({"ok": False, "error": "返工需要有效的原任务"}, status_code=400)
        mode = "rework" if job_id else mode
        task_ids = body.get("task_ids") or []
        if not isinstance(task_ids, list) or any(not isinstance(t, str) or not t for t in task_ids):
            return JSONResponse({"ok": False, "error": "task_ids 必须是任务编号数组"}, status_code=400)
        task_ids = list(dict.fromkeys(task_ids))
        defaults = bench.defaults()
        subject_choice = subject_mod.normalise(body.get("subject"))
        subject_kind = subject_choice if subject_choice in {"garment", "person"} else ""
        model_choice = str(body.get("model") or "").strip()
        provider_name = str(body.get("provider") or "").strip()
        history = [
            {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
            for item in (body.get("history") or []) if isinstance(item, dict)
        ]
        garment = None
        color = ""
        image_paths: list[Path] = []
        image_assets: list[HarnessImage] = []
        image_notes: list[str] = []
        target_prompts: list[dict] = []
        task_context: list[dict] | None = None
        inherited: dict = {}
        try:
            if job_id:
                job = bench.get_job(job_id)
                inherited = job.params
                ready = [t for t in job.tasks if t.out and t.id in task_ids]
                if task_ids and set(task_ids) != {t.id for t in ready}:
                    raise ValueError("选中的返工任务不存在或尚无成品")
                task_ids = [t.id for t in ready]
                count = len(ready)
                source_count = len({str(t.source) for t in ready})
                task_context = []
                rows = []
                for task in ready:
                    picked = bench.skill.type_by_alias(task.garment or inherited.get("garment_type"))
                    # Rework colors may have a -v2 suffix; retain the actual task metadata.
                    label = str(task.color)
                    while label.endswith("-v2"):
                        label = label.removesuffix("-v2")
                    ref = picked.color(label)
                    code = ref.file_code if ref else ""
                    task_context.append({"task_id": task.id, "garment_type": picked.slug,
                                         "garment_name": task.garment_name or picked.display_name,
                                         "color": task.color, "color_name": task.color_name,
                                         "hex": task.color_hex, "source": task.source,
                                         "code": code,
                                         "item": task.item, "view": task.view,
                                         "subject": getattr(task, "subject", "")})
                    rows.append({"garment_type": picked.slug, "color": task.color,
                                 "name_zh": task.color_name, "hex": task.color_hex, "code": code})
                    target_prompts.append({"task_id": task.id, "garment_type": picked.slug,
                                           "color": task.color, "prompt": task.prompt,
                                           "subject": subject_choice if subject_choice != "auto" else subject_mod.task_kind(job, task, bench.get_job)})
                parsed_selections = []
                for row in rows:
                    picked = bench.skill.type_by_alias(row["garment_type"])
                    selected = next((s for s in parsed_selections if s["garment_type"] == picked.slug), None)
                    if selected is None:
                        selected = {"garment_type": picked.slug, "garment_name": picked.display_name,
                                    "colors": [], "color_details": []}
                        parsed_selections.append(selected)
                    if row["color"] not in selected["colors"]:
                        selected["colors"].append(row["color"])
                        selected["color_details"].append({"label": row["color"], "name_zh": row["name_zh"],
                                                          "hex": row["hex"], "code": row["code"]})
                image_assets, image_notes = rework_images(job, ready, bench.get_job, limit=agent.MAX_IMAGES)
                image_paths = [row.path for row in image_assets]
                kinds = {subject_mod.task_kind(job, t, bench.get_job) for t in ready}
                subject_kind = subject_kind or (next(iter(kinds)) if len(kinds) == 1 else "")
                model_choice = model_choice or str(inherited.get("model") or "")
                provider_name = provider_name or str(inherited.get("provider") or "")
            else:
                parsed_selections = validate_selections(bench.skill, body)
                uploads = bench.draft_image_paths(draft_id) if draft_id else []
                source_count = len(uploads)
                count = source_count * sum(len(row["colors"]) for row in parsed_selections)
                image_paths = uploads[:agent.MAX_IMAGES]
                image_assets = [HarnessImage(path, "upload", f"原始上传图 · {path.name}") for path in image_paths]
                if len(uploads) > len(image_paths):
                    image_notes.append(f"本轮只附带 {len(image_paths)}/{len(uploads)} 个原图文件，不能声称已全部看过。")
                if image_paths:
                    subject_kind = subject_mod.detect(image_paths[0], subject_choice)
            if parsed_selections:
                garment = bench.skill.type_by_alias(parsed_selections[0]["garment_type"])
            if sum(len(row["colors"]) for row in parsed_selections) == 1:
                color = parsed_selections[0]["color_details"][0]["name_zh"]
        except (KeyError, ValueError, WorkbenchError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        def setting(name: str, fallback):
            return body[name] if name in body and body[name] not in (None, "") else inherited.get(name, fallback)

        pattern_mode = str(setting("pattern_mode", "") or prompts.detect_pattern_mode(instruction) or "preserve")
        if pattern_mode not in prompts.PATTERN_MODES:
            return JSONResponse({"ok": False, "error": "未知图案策略"}, status_code=400)
        provider_obj = bench.get_provider(provider_name or None)
        model_choice = model_choice or str(provider_obj.cfg.get("model") or "")
        settings = {
            "provider": provider_obj.name, "model": model_choice,
            "size": str(setting("size", "auto")), "quality": str(setting("quality", "high")),
            "output_format": str(setting("output_format", defaults.get("output_format") or "png")),
            "subject_requested": subject_choice, "subject_resolved": subject_kind or "auto",
            "pattern_mode": pattern_mode, "pattern_brief": str(setting("pattern_brief", "")),
            "send_reference": setting("send_reference", True), "shape_lock": str(setting("shape_lock", "")),
            "concurrency": setting("concurrency", "auto"), "generation_mode": str(setting("generation_mode", "single_pass")),
        }
        optional_settings = {name: setting(name, None) for name in
                             ("rpm", "seed", "max_retries", "timeout_sec", "input_fidelity")
                             if name in body or name in inherited}
        settings.update(optional_settings)
        estimate = bench.pricer.estimate(model_choice, count, settings["size"], provider=provider_obj.name)
        context = batch_context(selections=parsed_selections, source_count=source_count,
                                output_count=count, settings=settings, estimate=estimate, tasks=task_context)
        if not job_id:
            for selected in parsed_selections:
                picked = bench.skill.type_by_alias(selected["garment_type"])
                for label in selected["colors"]:
                    ref = picked.color(label)
                    prompt = prompts.build_single_pass(
                        bench.skill, picked, ref, instruction if instruction and not harness.looks_like_question(instruction) else None,
                        pattern_mode=pattern_mode, pattern_brief=settings["pattern_brief"], subject=subject_kind,
                    )
                    target_prompts.append({"garment_type": picked.slug, "color": label, "prompt": prompt})
                    target_prompts[-1]["subject"] = subject_kind
        # A single-task legacy preview is safe; multi-target previews must not be first-color prompts.
        base_prompt = target_prompts[0]["prompt"] if len(target_prompts) == 1 else batch_prompt(context)
        agent_cfg = defaults.get("harness") or {}
        agent_key = str(bench.env.get(str(agent_cfg.get("api_key_env") or ""), "")).strip()
        agent_model = str(agent_cfg.get("model") or "").strip()

        def sse(event: dict) -> bytes:
            payload = json.dumps(event["data"], ensure_ascii=False)
            return f"event: {event['event']}\ndata: {payload}\n\n".encode("utf-8")

        async def local_frames(reason: str = ""):
            if reason:
                yield sse({"event": "delta", "data": {"text": f"（{reason}，改用本地分析）\n\n"}})
            if image_assets:
                yield sse({"event": "delta", "data": {"text": "本地降级只分析需求文字，不具备原图／成品视觉识别能力；不假定已确认正反面内容。\n\n"}})
            async for event in harness.stream(
                mode=mode,
                instruction=instruction,
                base_prompt=base_prompt,
                garment=garment.display_name if garment else "",
                color=color,
                count=count,
                llm_cfg={},
                env=bench.env,
                batch_context=context,
                target_prompts=target_prompts,
            ):
                yield sse(event)

        created = {"job_id": "", "requirements": ""}

        async def agent_frames(config: dict):
            ctx = agent.AgentContext(
                mode=mode,
                garment=garment.display_name if garment else "",
                color=color,
                count=count,
                selections=job_selections(parsed_selections),
                batch_context=context,
                target_prompts=target_prompts,
                model=model_choice,
                base_prompt=base_prompt,
                draft_id=draft_id,
                job_id=job_id,
                task_ids=task_ids,
                pattern_mode=pattern_mode,
                pattern_modes=list(prompts.PATTERN_MODES),
                subject=subject_kind or subject_choice,
            )

            async def tool_list_models() -> str:
                data = await bench.list_models(provider_obj.name)
                rows = [m for m in data["models"] if m.get("image")]
                lines = [
                    f"{m['id']} · {m.get('price_label') or '未定价'}"
                    for m in rows
                ]
                return f"当前 {len(rows)} 个图片模型：\n" + "\n".join(lines[:30])

            inspection = None
            attached_assets: list[HarnessImage] = []

            async def tool_inspect() -> str:
                nonlocal inspection
                if inspection is None:
                    inspection = await asyncio.to_thread(inspect_images, attached_assets, image_notes)
                return inspection

            async def tool_estimate(model: str, amount: int) -> str:
                est = bench.pricer.estimate(
                    model or model_choice, count, settings["size"], provider=provider_obj.name,
                )
                scope = f"{provider_obj.name} / {settings['size']}；当前完整批次 {count} 张"
                if not est["priced"]:
                    return f"{scope}；{est['model']} 单价未知、总价未知（未定价）"
                tier = f" · {est['tier']} 档" if est.get("tier") else ""
                return (
                    f"{scope}；{est['model']} × {est['count']} 张 = {est['total']} {est['currency']}"
                    f"（单价 {est['unit']} {est['currency']}/张{tier}）；仅报价，不修改当前模型或选择"
                )

            async def tool_submit(extra: str) -> str:
                if created["job_id"]:
                    return f"任务 {created['job_id']} 已创建，请勿重复提交"
                if mode != "intake":
                    return "失败：当前是返工，请使用 submit_rework"
                if not draft_id:
                    return "失败：没有待生成的图片草稿"
                if not parsed_selections:
                    return "失败：尚未选择款式和颜色，请在工作台选择后再提交"
                uploads = await asyncio.to_thread(bench.draft_uploads, draft_id)
                job = await asyncio.to_thread(
                    bench.create_job,
                    uploads,
                    garment_type=parsed_selections[0]["garment_type"],
                    selections=job_selections(parsed_selections),
                    provider=provider_obj.name,
                    model=model_choice,
                    concurrency=settings["concurrency"],
                    mode=settings["generation_mode"],
                    size=settings["size"],
                    quality=settings["quality"],
                    output_format=settings["output_format"],
                    extra_requirements=extra,
                    pattern_mode=str(ctx.pattern_mode or pattern_mode),
                    pattern_brief=settings["pattern_brief"],
                    subject=subject_choice,
                    send_reference=settings["send_reference"],
                    shape_lock=settings["shape_lock"],
                    **optional_settings,
                    account=current_account(request),
                )
                created["job_id"] = job.id
                bench.start(job.id)
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
                if created["job_id"]:
                    return f"任务 {created['job_id']} 已创建，请勿重复提交"
                if mode != "rework" or not job_id or not task_ids:
                    return "失败：没有选中的返工成品"
                job = await bench.create_rework_job(
                    job_id, task_ids, text or instruction or "按需求调整",
                    provider=provider_obj.name, model=model_choice,
                    subject=subject_choice,
                    size=settings["size"],
                    quality=settings["quality"],
                    output_format=settings["output_format"],
                    pattern_mode=str(ctx.pattern_mode or pattern_mode),
                    account=current_account(request),
                )
                created["job_id"] = job.id
                bench.start(job.id)
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
            for row in image_assets:
                data_url = await asyncio.to_thread(agent.encode_image, row.path)
                if data_url:
                    ctx.images.append(data_url)
                    ctx.image_labels.append(row.label)
                    attached_assets.append(row)
                else:
                    image_notes.append(f"{row.label} 编码失败，未发送给在线模型，不能声称已看到内容。")
            ctx.image_notes = " ".join(image_notes)

            yield sse({"event": "meta", "data": {"mode": mode, "count": count, "batch_context": context}})
            async for event in agent.run_agent(
                user_message=instruction,
                ctx=ctx,
                history=history,
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
                    data["batch_context"] = context
                    data["target_prompts"] = data.get("target_prompts") or target_requirements(
                        target_prompts, data["requirements"], pattern_mode=ctx.pattern_mode, subject=ctx.subject,
                    )
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
                if created["job_id"]:
                    yield sse({"event": "job", "data": {"job_id": created["job_id"]}})
                    yield sse({"event": "delta", "data": {"text": "任务已创建；后续对话连接异常，请查看任务记录，勿重复提交。"}})
                    return
                async for chunk in local_frames(f"在线模型不可用：{type(exc).__name__}"):
                    yield chunk

        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------- 飞书历史/回拉 --
    @app.get("/api/feishu/records")
    async def feishu_records(
        request: Request, limit: int = 200, job_id: str = "", color: str = "", links: bool = False
    ):
        """读飞书多维表格里的历史成品记录（Render 重启后也能看到）。

        `links=true` 时额外返回 `download_url`（飞书签发的临时直链）——
        浏览器直接找飞书要图，**不经过 Render**，打包/逐张下载都不吃免费流量。
        """
        bench = wb(request)
        cfg = feishu.load_config(bench.config, bench.env)
        ok, reason = cfg.ready()
        if not ok:
            return JSONResponse(
                {"ok": False, "error": f"飞书未配置：{reason}"}, status_code=400
            )
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None,
            lambda: feishu_export.list_records(
                cfg, limit=max(1, min(500, limit)), job_id=job_id, color=color
            ),
        )
        if links:
            tokens = [str(r.get("file_token") or "") for r in records]
            try:
                mapping = await loop.run_in_executor(
                    None, lambda: feishu_export.tmp_download_urls(cfg, tokens)
                )
            except Exception as exc:  # noqa: BLE001 - 拿不到直链就只给打包
                mapping = {}
                print(f"[feishu] 直链获取失败：{exc}", flush=True)
            for record in records:
                record["download_url"] = mapping.get(str(record.get("file_token") or ""), "")
        return {
            "ok": True,
            "count": len(records),
            "records": records,
            "links": bool(links),
            "table": f"https://feishu.cn/base/{cfg.app_token}?table={cfg.table_id}",
        }

    @app.post("/api/feishu/export")
    async def feishu_export_zip(request: Request):
        """从飞书附件批量拉成品图，打包成 ZIP 直接下载（不依赖本机 data/）。"""
        bench = wb(request)
        cfg = feishu.load_config(bench.config, bench.env)
        ok, reason = cfg.ready()
        if not ok:
            return JSONResponse(
                {"ok": False, "error": f"飞书未配置：{reason}"}, status_code=400
            )
        body = await request.json()
        record_ids = {str(x) for x in (body.get("record_ids") or [])}
        job_id = str(body.get("job_id") or "")
        color = str(body.get("color") or "")
        limit = int(body.get("limit") or 500)

        def work() -> bytes:
            records = feishu_export.list_records(
                cfg, limit=max(1, min(500, limit)), job_id=job_id, color=color
            )
            if record_ids:
                records = [r for r in records if str(r.get("record_id")) in record_ids]
            folder = f"feishu-{job_id}" if job_id else "feishu-history"
            return feishu_export.build_zip(cfg, records, folder=folder)

        try:
            payload = await asyncio.get_running_loop().run_in_executor(None, work)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        name = f"feishu-{job_id}.zip" if job_id else "feishu-history.zip"
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
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
        pattern_mode: str = Form(""),
        subject: str = Form(""),
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
            account=current_account(request),
            pattern_mode=pattern_mode,
            subject=subject,
        )
        if start:
            bench.start(job.id)
        return {"ok": True, "job": job.as_dict(with_tasks=True, with_log=True)}

    @app.get("/api/jobs/{job_id}/export")
    async def export_selected(request: Request, job_id: str, tasks: str = "", fmt: str = ""):
        bench = wb(request)
        ids = [part.strip() for part in tasks.split(",") if part.strip()]
        path = await asyncio.to_thread(bench.export_zip, job_id, ids, fmt)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    # ---------------------------------------------------------------- files --
    @app.get("/api/jobs/{job_id}/image/{task_id}")
    async def job_image(
        request: Request, job_id: str, task_id: str, download: bool = False,
        fmt: str = "", w: int = 0, q: int = 85,
    ):
        bench = wb(request)
        path = bench.image_path(job_id, task_id)
        # fmt=jpg|webp 时给压缩版（PNG 3~6MB → 0.3~0.8MB），原图仍留在服务器
        if str(fmt).lower() in {"jpg", "jpeg", "webp"}:
            derived = await asyncio.to_thread(
                bench.derived_image,
                job_id,
                task_id,
                width=max(0, min(3000, int(w or 0))),
                fmt="webp" if str(fmt).lower() == "webp" else "jpg",
                quality=max(60, min(95, int(q or 85))),
            )
            if derived is not None:
                return FileResponse(
                    derived,
                    filename=derived.name if download else None,
                    media_type="image/webp" if str(fmt).lower() == "webp" else "image/jpeg",
                    headers={"Cache-Control": "private, max-age=31536000, immutable"},
                )
        if download:
            return FileResponse(path, filename=path.name)
        # 成品图内容不会变（版本由 URL 的 ?v= 区分），可以让浏览器长期缓存
        return FileResponse(
            path,
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @app.get("/api/jobs/{job_id}/thumb/{task_id}")
    async def job_thumb(request: Request, job_id: str, task_id: str, w: int = 520):
        """列表用缩略图（WebP）。原图动辄 3~6MB，缩略图只有几十 KB —— 省 Render 出口流量。"""
        bench = wb(request)
        path = bench.image_path(job_id, task_id)
        width = max(120, min(2400, int(w or 520)))
        thumb_path = await asyncio.to_thread(bench.thumbnail_path, job_id, task_id, width)
        if thumb_path is None:
            return FileResponse(path, headers={"Cache-Control": "private, max-age=600"})
        return FileResponse(
            thumb_path,
            media_type="image/webp",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @app.post("/api/jobs/{job_id}/feishu")
    async def job_feishu_sync(request: Request, job_id: str, force: bool = False):
        """补写飞书：自动同步失败（或免费实例被休眠打断）时手动补一次。

        默认不 force：已经写过的任务不会重复写（避免飞书里出现重复行）。
        """
        bench = wb(request)
        job = bench.get_job(job_id)
        rows = await asyncio.to_thread(bench.sync_to_feishu, job, force)
        return {
            "ok": True,
            "rows": rows,
            "already": bool(job.params.get("feishu_rows")) and not force,
            "table": job.params.get("feishu_table") or "",
            "pending": bench.pending_sync(),
        }

    @app.post("/api/feishu/sync-pending")
    async def feishu_sync_pending(request: Request):
        bench = wb(request)
        rows = await asyncio.to_thread(bench.sync_pending)
        return {"ok": True, "rows": rows, "pending": bench.pending_sync()}

    @app.get("/api/jobs/{job_id}/download")
    async def download(request: Request, job_id: str):
        path = wb(request).zip_path(job_id)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    return app


__all__ = ["create_app", "load_skill"]
