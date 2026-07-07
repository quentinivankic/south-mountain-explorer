/**
 * Cloudflare Worker — PMTiles range-request handler (spec §7.4).
 *
 * Serves per-region .pmtiles archives from an R2 bucket via HTTP range
 * requests, so no tile server is needed and R2 egress is free. The app
 * (or a MapLibre/pmtiles client) requests byte ranges; this Worker proxies
 * them to R2 and adds CORS + caching.
 *
 * Bind an R2 bucket named `TILES` in wrangler.toml. Objects are keyed
 * `regions/<region>.pmtiles` (matches dist/regions/ layout).
 *
 * Routes:
 *   GET /regions/<region>.pmtiles         (Range supported; 206 responses)
 *   GET /attributions.json                (the generated per-region strings)
 *   GET /healthz
 *
 * This only SERVES bytes. The licensing gate ran at build time — nothing
 * that failed it is in the bucket to begin with.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Range, If-Match, If-None-Match",
  "Access-Control-Expose-Headers": "Content-Range, Content-Length, ETag, Accept-Ranges",
};

function parseRange(header, size) {
  // Single-range only ("bytes=start-end"); that's all pmtiles clients use.
  const m = /^bytes=(\d*)-(\d*)$/.exec(header || "");
  if (!m) return null;
  let [, a, b] = m;
  if (a === "" && b === "") return null;
  let start, end;
  if (a === "") {
    // suffix range: last N bytes
    end = size - 1;
    start = Math.max(0, size - parseInt(b, 10));
  } else {
    start = parseInt(a, 10);
    end = b === "" ? size - 1 : Math.min(parseInt(b, 10), size - 1);
  }
  if (isNaN(start) || isNaN(end) || start > end || start >= size) return null;
  return { start, end };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: CORS });
    }

    const url = new URL(request.url);
    const path = url.pathname.replace(/^\/+/, "");

    if (path === "healthz") {
      return new Response("ok", { status: 200, headers: CORS });
    }

    // Only serve the two known object shapes.
    const allowed = path === "attributions.json" || /^regions\/[\w.-]+\.pmtiles$/.test(path);
    if (!allowed) {
      return new Response("Not Found", { status: 404, headers: CORS });
    }

    const rangeHeader = request.headers.get("Range");

    if (rangeHeader) {
      const head = await env.TILES.head(path);
      if (!head) return new Response("Not Found", { status: 404, headers: CORS });
      const range = parseRange(rangeHeader, head.size);
      if (!range) {
        return new Response("Range Not Satisfiable", {
          status: 416,
          headers: { ...CORS, "Content-Range": `bytes */${head.size}` },
        });
      }
      const obj = await env.TILES.get(path, {
        range: { offset: range.start, length: range.end - range.start + 1 },
      });
      return new Response(request.method === "HEAD" ? null : obj.body, {
        status: 206,
        headers: {
          ...CORS,
          "Accept-Ranges": "bytes",
          "Content-Type": path.endsWith(".json") ? "application/json" : "application/octet-stream",
          "Content-Length": String(range.end - range.start + 1),
          "Content-Range": `bytes ${range.start}-${range.end}/${head.size}`,
          "ETag": head.httpEtag,
          "Cache-Control": "public, max-age=86400",
        },
      });
    }

    const obj = await env.TILES.get(path);
    if (!obj) return new Response("Not Found", { status: 404, headers: CORS });
    return new Response(request.method === "HEAD" ? null : obj.body, {
      status: 200,
      headers: {
        ...CORS,
        "Accept-Ranges": "bytes",
        "Content-Type": path.endsWith(".json") ? "application/json" : "application/octet-stream",
        "Content-Length": String(obj.size),
        "ETag": obj.httpEtag,
        "Cache-Control": "public, max-age=86400",
      },
    });
  },
};
