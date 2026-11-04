/**
 * 生图工作台 · 全局并发阀门（Cloudflare Worker + Durable Object）
 *
 * 作用：所有电脑共用一把生图网关 Key，网关实测只稳定支持 20 并发。
 *       这个 Worker 就是唯一那把「并发令牌盒」：谁要发请求，先来这里拿一张牌，
 *       满了就排队；跑完立刻还牌。单实例 Durable Object 天然串行，不会出现两个
 *       机器同时拿到最后一张牌的情况。
 *
 * 部署：见同目录 README.md（wrangler deploy 即可，免费额度够用）。
 *
 * 协议（客户端在 skillbridge/gate.py）：
 *   POST /        {"action":"acquire","holder":"PC-A","job":"...","task":"...","limit":20}
 *     → 200 {"ok":true,"lease":"<uuid>","in_use":7,"limit":20}
 *     → 429 {"ok":false,"in_use":20,"limit":20,"retry_after_ms":800}
 *   POST /        {"action":"release","lease":"<uuid>","holder":"PC-A"}
 *     → 200 {"ok":true,"in_use":19}
 *   POST /        {"action":"status"}
 *     → 200 {"ok":true,"in_use":3,"limit":20,"leases":[...]}
 *
 * 鉴权：Authorization: Bearer <GATE_TOKEN>（Worker 的 secret）。
 *      没配 GATE_TOKEN 时只允许 status（方便先试通）。
 *
 * 租约 TTL：默认 180 秒。客户端每次发请求前都会 acquire（= 续租），
 *          所以机器断电/进程崩了最多 180 秒后自动释放，不会永久占坑。
 */

const DEFAULT_LIMIT = 20;
const LEASE_TTL_MS = 180_000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({ ok: true, service: "workbench-gate" });
    }
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors() });
    }
    if (request.method !== "POST") {
      return json({ ok: false, error: "use POST" }, 405);
    }

    const token = env.GATE_TOKEN || "";
    if (token) {
      const auth = request.headers.get("Authorization") || "";
      if (auth !== `Bearer ${token}`) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
    }

    let body = {};
    try {
      body = await request.json();
    } catch (_) {
      return json({ ok: false, error: "invalid json" }, 400);
    }
    const action = String(body.action || "status");
    if (!token && action !== "status") {
      return json({ ok: false, error: "GATE_TOKEN 未配置，只允许 status" }, 403);
    }

    // 所有请求都落到同一个 Durable Object（全局唯一那把锁）
    const id = env.GATE.idFromName("global");
    const stub = env.GATE.get(id);
    return stub.fetch("https://gate/do", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, action, now: Date.now() }),
    });
  },
};

export class Gate {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    /** @type {Map<string, {holder:string, job:string, task:string, expires:number}>} */
    this.leases = new Map();
  }

  async fetch(request) {
    const body = await request.json();
    const limit = clampLimit(body.limit ?? this.env.GATE_LIMIT ?? DEFAULT_LIMIT);
    const now = Number(body.now) || Date.now();
    this.gc(now);

    if (body.action === "status") {
      return json({
        ok: true,
        in_use: this.leases.size,
        limit,
        leases: [...this.leases.entries()].map(([id, info]) => ({
          id,
          holder: info.holder,
          expires_in: Math.max(0, Math.round((info.expires - now) / 1000)),
        })),
      });
    }

    if (body.action === "release") {
      const id = String(body.lease || "");
      const removed = this.leases.delete(id);
      return json({ ok: true, released: removed, in_use: this.leases.size, limit });
    }

    if (body.action !== "acquire") {
      return json({ ok: false, error: `unknown action ${body.action}` }, 400);
    }

    if (this.leases.size >= limit) {
      // 满员：告诉客户端等多久再来（带一点随机，避免同时冲进来）
      const retry = 600 + Math.floor(Math.random() * 500);
      return json(
        { ok: false, in_use: this.leases.size, limit, retry_after_ms: retry },
        429,
        { "Retry-After": String(Math.ceil(retry / 1000)) }
      );
    }

    const lease = crypto.randomUUID();
    this.leases.set(lease, {
      holder: String(body.holder || "unknown").slice(0, 64),
      job: String(body.job || "").slice(0, 64),
      task: String(body.task || "").slice(0, 96),
      expires: now + LEASE_TTL_MS,
    });
    return json({ ok: true, lease, in_use: this.leases.size, limit });
  }

  /** 清掉过期租约（机器崩了不会永久占坑） */
  gc(now) {
    for (const [id, info] of this.leases) {
      if (info.expires <= now) this.leases.delete(id);
    }
  }
}

function clampLimit(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 1) return DEFAULT_LIMIT;
  return Math.min(200, Math.floor(n));
}

function json(payload, status = 200, extra = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json", ...cors(), ...extra },
  });
}

function cors() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };
}
