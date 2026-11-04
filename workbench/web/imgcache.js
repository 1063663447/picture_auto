/* 本地图片缓存（5.6）
 * 目的：出图后第一次读服务器，之后（包括刷新/切历史任务）直接从本地 IndexedDB 读，
 * 不再每次 DOM 重建都回服务器拉一遍。
 *
 * 用法：
 *   const url = await WBImageCache.resolve({ jobId, taskId, url: "/api/jobs/.../image/..." })
 *   img.src = url;                       // blob: 地址
 *   WBImageCache.stats()                 // { count, bytes }
 *   await WBImageCache.clear()           // 清空
 *
 * 特性：
 *  - 以 `jobId::taskId` 为主键，附带版本号（服务端的 ?t=...），版本变了才重新下载；
 *  - 单张 ≤ 12MB（超过就不缓存，避免撑爆配额）；
 *  - 总容量上限 400MB，超了按最后访问时间淘汰最旧的；
 *  - 同一个 key 并发请求只下载一次。
 */
(function () {
  "use strict";

  const DB_NAME = "wb-image-cache";
  const STORE = "blobs";
  const MAX_BYTES = 400 * 1024 * 1024;
  const MAX_ITEM = 12 * 1024 * 1024;

  let dbPromise = null;
  const objectUrls = new Map();      // key -> blob url
  const inflight = new Map();        // key -> Promise<string>

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      if (!window.indexedDB) {
        reject(new Error("no indexedDB"));
        return;
      }
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "key" });
          store.createIndex("at", "at");
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("indexedDB open failed"));
    }).catch((error) => {
      console.warn("[imgcache] 不可用，退回直连", error);
      return null;
    });
    return dbPromise;
  }

  function tx(db, mode) {
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  function wrap(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function keyOf(jobId, taskId) {
    return `${jobId}::${taskId}`;
  }

  function versionOf(url) {
    const match = /[?&]t=(\d+)/.exec(url || "");
    return match ? match[1] : "";
  }

  async function all() {
    const db = await openDb();
    if (!db) return [];
    return wrap(tx(db, "readonly").getAll());
  }

  async function stats() {
    const rows = await all();
    return {
      count: rows.length,
      bytes: rows.reduce((sum, row) => sum + (row.blob ? row.blob.size : 0), 0),
    };
  }

  async function evict(db) {
    const rows = await all();
    const total = rows.reduce((sum, row) => sum + (row.blob ? row.blob.size : 0), 0);
    if (total <= MAX_BYTES) return;
    rows.sort((a, b) => (a.at || 0) - (b.at || 0));
    let size = total;
    const store = tx(db, "readwrite");
    for (const row of rows) {
      if (size <= MAX_BYTES * 0.8) break;
      size -= row.blob ? row.blob.size : 0;
      store.delete(row.key);
      revoke(row.key);
    }
  }

  function revoke(key) {
    const url = objectUrls.get(key);
    if (url) {
      URL.revokeObjectURL(url);
      objectUrls.delete(key);
    }
  }

  async function put(key, version, blob) {
    if (!blob || blob.size > MAX_ITEM) return;
    const db = await openDb();
    if (!db) return;
    try {
      await wrap(
        tx(db, "readwrite").put({ key, version, blob, at: Date.now(), bytes: blob.size })
      );
      await evict(db);
    } catch (error) {
      console.warn("[imgcache] 写入失败（可能配额已满）", error);
    }
  }

  async function resolve({ jobId, taskId, url }) {
    const key = keyOf(jobId, taskId);
    const version = versionOf(url);
    const cachedUrl = objectUrls.get(key);
    if (cachedUrl) {
      const db = await openDb();
      if (db) {
        const row = await wrap(tx(db, "readonly").get(key));
        if (row && row.version === version) return cachedUrl;
      }
    }
    if (inflight.has(key)) return inflight.get(key);

    const pending = (async () => {
      const db = await openDb();
      let row = null;
      if (db) {
        try {
          row = await wrap(tx(db, "readonly").get(key));
        } catch (_) {
          row = null;
        }
      }
      if (row && row.blob && row.version === version) {
        revoke(key);
        const localUrl = URL.createObjectURL(row.blob);
        objectUrls.set(key, localUrl);
        // 顺手刷新访问时间，LRU 淘汰时不会先删刚看过的
        put(key, version, row.blob);
        return localUrl;
      }
      const response = await fetch(url, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      await put(key, version, blob);
      revoke(key);
      const localUrl = URL.createObjectURL(blob);
      objectUrls.set(key, localUrl);
      return localUrl;
    })().finally(() => inflight.delete(key));

    inflight.set(key, pending);
    return pending;
  }

  async function clear() {
    objectUrls.forEach((url) => URL.revokeObjectURL(url));
    objectUrls.clear();
    const db = await openDb();
    if (!db) return;
    await wrap(tx(db, "readwrite").clear());
  }

  window.WBImageCache = { resolve, stats, clear, versionOf };
})();
