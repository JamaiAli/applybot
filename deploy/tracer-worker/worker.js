/**
 * ApplyPilot — traceur de liens de CV, hébergé sur Cloudflare Workers.
 *
 * Un recruteur clique sur un lien du CV : https://<worker>/t/<token>
 *   1. le clic est enregistré (stockage KV) ;
 *   2. une alerte Telegram part directement d'ici — le PC du candidat peut être éteint ;
 *   3. le recruteur est redirigé vers la vraie page (LinkedIn, GitHub, portfolio).
 *
 * Le bot ApplyPilot parle à ce Worker par /api/* (protégé par une clé partagée) :
 *   POST /api/register  → déclare les liens d'une offre avant d'envoyer le CV
 *   GET  /api/hits      → relit les clics, pour /trackers
 *
 * Liaisons attendues (wrangler.toml + secrets) :
 *   TRACKERS            espace KV
 *   API_KEY             clé partagée avec le bot (secret)
 *   TELEGRAM_BOT_TOKEN  jeton du bot (secret) — sans lui, pas d'alerte, mais le suivi marche
 *   TELEGRAM_USER_ID    destinataire des alertes (secret)
 *   LINKEDIN_URL, GITHUB_URL, PORTFOLIO_URL   pages du candidat (variables)
 */

const PERMANENT = { linkedin: "LINKEDIN_URL", github: "GITHUB_URL", portfolio: "PORTFOLIO_URL" };
const LABELS = {
  linkedin: ["💼", "Profil LinkedIn"],
  github: ["🐙", "Projet GitHub"],
  portfolio: ["🌐", "Portfolio"],
};
// Robots qui ouvrent les liens d'un PDF pour les analyser (antivirus de messagerie,
// aperçus de lien, scanners de portails) : comptés à part, pour ne pas crier victoire.
const BOT_UA = /bot|crawl|spider|preview|scan|monitor|curl|wget|python|httpclient|headless|okhttp|go-http|java\/|proofpoint|mimecast|barracuda|safelinks|urldefense|facebookexternalhit|slack|whatsapp|telegram|skype|teams/i;

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8" } });

const redirect = (target) =>
  new Response(null, { status: 302, headers: { location: target, "cache-control": "no-store" } });

const escapeHtml = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function fallbackUrl(env) {
  return env.PORTFOLIO_URL || "https://jamaiali.github.io";
}

function isHttpsUrl(value) {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function authorized(request, env) {
  const key = env.API_KEY || "";
  const given = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  if (!key || given.length !== key.length) return false;
  let diff = 0; // comparaison à temps constant
  for (let i = 0; i < key.length; i++) diff |= key.charCodeAt(i) ^ given.charCodeAt(i);
  return diff === 0;
}

export function describeDevice(ua) {
  const s = String(ua || "");
  if (!s) return "Appareil inconnu";
  const os = /iphone|ipad/i.test(s) ? "iPhone / iPad" : /android/i.test(s) ? "Android"
    : /windows/i.test(s) ? "Windows" : /mac os x|macintosh/i.test(s) ? "Mac" : /linux/i.test(s) ? "Linux" : "";
  const browser = /edg\//i.test(s) ? "Edge" : /chrome\//i.test(s) ? "Chrome" : /firefox\//i.test(s) ? "Firefox"
    : /safari\//i.test(s) ? "Safari" : "";
  return [os, browser].filter(Boolean).join(" · ") || s.slice(0, 40);
}

async function loadTracker(token, env) {
  if (PERMANENT[token]) {
    return {
      token,
      target: env[PERMANENT[token]] || fallbackUrl(env),
      link_type: token,
      company: "Lien direct / profil public",
      title: `Profil ${token[0].toUpperCase()}${token.slice(1)}`,
      job_url: "profil_permanent",
    };
  }
  return env.TRACKERS.get(`t:${token}`, "json");
}

export function formatAlert(tracker, hit) {
  const [icon, label] = LABELS[tracker.link_type] || ["🔗", tracker.link_type || "Lien"];
  const when = new Date(hit.ts).toLocaleString("fr-FR", { timeZone: "Europe/Paris" });
  const where = [hit.city, hit.country].filter(Boolean).join(", ");
  const lines = [
    hit.bot ? `🤖 <b>Ouverture automatique de ton ${label}</b> (probablement un robot, pas un recruteur)`
            : `🔔 <b>Consultation de ton ${label} détectée !</b>`,
    "",
    `🎯 <b>Élément consulté :</b> ${icon} ${label}`,
    `🏢 <b>Entreprise :</b> ${escapeHtml(tracker.company || "Entreprise")}`,
    `💼 <b>Offre :</b> ${escapeHtml(tracker.title || "Offre")}`,
    `📱 <b>Appareil :</b> ${escapeHtml(describeDevice(hit.ua))}`,
  ];
  if (where) lines.push(`📍 <b>Lieu approximatif :</b> ${escapeHtml(where)}`);
  lines.push(`⏰ <b>Date :</b> ${when}`);
  if (tracker.job_url && tracker.job_url !== "profil_permanent" && isHttpsUrl(tracker.job_url)) {
    lines.push(`🔗 <a href="${escapeHtml(tracker.job_url)}">Voir l'offre associée</a>`);
  }
  return lines.join("\n");
}

async function notifyTelegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_USER_ID) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_USER_ID, text, parse_mode: "HTML", disable_web_page_preview: true,
      }),
    });
  } catch (err) {
    console.log("Alerte Telegram non envoyée :", err);
  }
}

async function recordHit(tracker, hit, env) {
  // Une clé par clic, les détails dans les métadonnées : un seul appel « list »
  // suffit ensuite au bot pour tout relire, sans lecture-modification-écriture.
  const key = `h:${String(Date.parse(hit.ts)).padStart(15, "0")}-${hit.id}`;
  await env.TRACKERS.put(key, "", { metadata: hit });
  await notifyTelegram(env, formatAlert(tracker, hit));
}

async function handleClick(token, request, env, ctx) {
  const tracker = await loadTracker(token, env);
  if (!tracker || !isHttpsUrl(tracker.target)) return redirect(fallbackUrl(env)); // lien inconnu : jamais de page morte
  if (request.method === "GET") {
    const ua = request.headers.get("user-agent") || "";
    const cf = request.cf || {};
    const hit = {
      id: crypto.randomUUID().slice(0, 8),
      token,
      ts: new Date().toISOString(),
      ip: request.headers.get("cf-connecting-ip") || "",
      ua: ua.slice(0, 300),
      country: cf.country || "",
      city: cf.city || "",
      bot: BOT_UA.test(ua) || !ua,
    };
    // Le recruteur n'attend pas l'enregistrement : il est redirigé tout de suite.
    ctx.waitUntil(recordHit(tracker, hit, env));
  }
  return redirect(tracker.target);
}

async function handleRegister(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "JSON invalide" }, 400);
  }
  const trackers = Array.isArray(body?.trackers) ? body.trackers : [];
  let registered = 0;
  for (const t of trackers.slice(0, 50)) {
    const token = String(t?.token || "").toLowerCase();
    if (!/^[a-z0-9_-]{4,40}$/.test(token) || PERMANENT[token] || !isHttpsUrl(t.target)) continue;
    await env.TRACKERS.put(`t:${token}`, JSON.stringify({
      token,
      target: t.target,
      link_type: String(t.link_type || "portfolio"),
      company: String(t.company || "").slice(0, 120),
      title: String(t.title || "").slice(0, 160),
      job_url: String(t.job_url || "").slice(0, 500),
    }));
    registered++;
  }
  return json({ ok: registered === trackers.length && registered > 0, registered });
}

async function handleHits(url, env) {
  const after = url.searchParams.get("after") || "";
  const hits = [];
  let cursor;
  do {
    const page = await env.TRACKERS.list({ prefix: "h:", cursor });
    for (const k of page.keys) if (k.name > after) hits.push({ key: k.name, ...(k.metadata || {}) });
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  hits.sort((a, b) => (a.key < b.key ? -1 : 1));
  return json({ ok: true, hits });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (path === "/health") return json({ status: "ok", service: "applypilot-tracer-worker" });

    if (path.startsWith("/api/")) {
      if (!authorized(request, env)) return json({ ok: false, error: "non autorisé" }, 401);
      if (path === "/api/register" && request.method === "POST") return handleRegister(request, env);
      if (path === "/api/hits" && request.method === "GET") return handleHits(url, env);
      return json({ ok: false, error: "route inconnue" }, 404);
    }

    const click = path.match(/^\/t\/([A-Za-z0-9_-]{1,40})$/);
    if (click && (request.method === "GET" || request.method === "HEAD")) {
      return handleClick(click[1].toLowerCase(), request, env, ctx);
    }
    return redirect(fallbackUrl(env));
  },
};
