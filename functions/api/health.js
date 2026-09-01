/**
 * EdgeOne Pages Function：/api/health
 * 返回 AI 服务是否就绪（密钥是否存在）。
 */
export async function onRequestGet({ env }) {
  const key = String(env.DEEPSEEK_API_KEY || "").trim();
  const code = String(env.DEMO_ACCESS_CODE || "").trim();
  return jsonResponse({
    ok: true,
    mode: "edgeone",
    ai: !!key,
    model: String(env.AI_MODEL || "deepseek-v4-flash"),
    envFile: true,
    needCode: !!code,
  });
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Demo-Code",
    },
  });
}
