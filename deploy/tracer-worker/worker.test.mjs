// Tests du Worker, sans Cloudflare : node --test deploy/tracer-worker/worker.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import worker, { describeDevice, formatAlert } from "./worker.js";

// --- Faux environnement Cloudflare -------------------------------------------
function makeEnv(extra = {}) {
  const store = new Map();
  const TRACKERS = {
    async get(key, type) {
      const entry = store.get(key);
      if (!entry) return null;
      return type === "json" ? JSON.parse(entry.value) : entry.value;
    },
    async put(key, value, options = {}) {
      store.set(key, { value, metadata: options.metadata });
    },
    async list({ prefix = "" } = {}) {
      const keys = [...store.keys()].filter((k) => k.startsWith(prefix)).sort()
        .map((name) => ({ name, metadata: store.get(name).metadata }));
      return { keys, list_complete: true };
    },
  };
  return {
    TRACKERS, store,
    API_KEY: "cle-de-test",
    LINKEDIN_URL: "https://www.linkedin.com/in/exemple",
    GITHUB_URL: "https://github.com/exemple",
    PORTFOLIO_URL: "https://exemple.github.io",
    ...extra,
  };
}

function makeCtx() {
  const pending = [];
  return { waitUntil: (p) => pending.push(p), done: () => Promise.all(pending) };
}

const BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36";

function req(path, { method = "GET", headers = {}, body } = {}) {
  return new Request(`https://suivi.exemple.workers.dev${path}`, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  });
}

const auth = { authorization: "Bearer cle-de-test", "content-type": "application/json" };
const TRACKER = {
  token: "ab12cd34", target: "https://www.linkedin.com/in/exemple", link_type: "linkedin",
  company: "Thales", title: "Stage - Analyste SOC", job_url: "https://example.com/offre/1",
};

async function register(env, trackers = [TRACKER]) {
  return worker.fetch(req("/api/register", { method: "POST", headers: auth, body: { trackers } }), env, makeCtx());
}

// --- Tests --------------------------------------------------------------------
test("un clic est enregistré, alerte Telegram envoyée, recruteur redirigé", async (t) => {
  const env = makeEnv({ TELEGRAM_BOT_TOKEN: "123:abc", TELEGRAM_USER_ID: "42" });
  const sent = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    sent.push({ url: String(url), body: JSON.parse(init.body) });
    return new Response("{}");
  });
  assert.equal((await register(env)).status, 200);

  const ctx = makeCtx();
  const res = await worker.fetch(
    req("/t/AB12CD34", { headers: { "user-agent": BROWSER, "cf-connecting-ip": "88.1.2.3" } }), env, ctx);
  await ctx.done();

  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), TRACKER.target);
  const hits = [...env.store.keys()].filter((k) => k.startsWith("h:"));
  assert.equal(hits.length, 1);
  const meta = env.store.get(hits[0]).metadata;
  assert.equal(meta.token, "ab12cd34");
  assert.equal(meta.ip, "88.1.2.3");
  assert.equal(meta.bot, false);

  assert.equal(sent.length, 1);
  assert.match(sent[0].url, /api\.telegram\.org\/bot123:abc\/sendMessage/);
  assert.equal(sent[0].body.chat_id, "42");
  assert.match(sent[0].body.text, /Thales/);
  assert.match(sent[0].body.text, /Analyste SOC/);
});

test("sans jeton Telegram, le clic est quand même compté et redirigé", async (t) => {
  const env = makeEnv();
  const fetchMock = t.mock.method(globalThis, "fetch", async () => new Response("{}"));
  await register(env);
  const ctx = makeCtx();
  const res = await worker.fetch(req("/t/ab12cd34", { headers: { "user-agent": BROWSER } }), env, ctx);
  await ctx.done();
  assert.equal(res.status, 302);
  assert.equal([...env.store.keys()].filter((k) => k.startsWith("h:")).length, 1);
  assert.equal(fetchMock.mock.callCount(), 0);
});

test("lien inconnu : redirection vers le portfolio, rien de compté", async () => {
  const env = makeEnv();
  const ctx = makeCtx();
  const res = await worker.fetch(req("/t/inconnu99", { headers: { "user-agent": BROWSER } }), env, ctx);
  await ctx.done();
  assert.equal(res.status, 302);
  assert.equal(res.headers.get("location"), "https://exemple.github.io");
  assert.equal(env.store.size, 0);
});

test("liens permanents : /t/github redirige et compte sans déclaration préalable", async () => {
  const env = makeEnv();
  const ctx = makeCtx();
  const res = await worker.fetch(req("/t/github", { headers: { "user-agent": BROWSER } }), env, ctx);
  await ctx.done();
  assert.equal(res.headers.get("location"), "https://github.com/exemple");
  assert.equal([...env.store.keys()].filter((k) => k.startsWith("h:")).length, 1);
});

test("un robot est signalé comme tel ; une requête HEAD n'est pas comptée", async () => {
  const env = makeEnv();
  await register(env);
  let ctx = makeCtx();
  await worker.fetch(req("/t/ab12cd34", { headers: { "user-agent": "Proofpoint URL Defense scanner" } }), env, ctx);
  await ctx.done();
  const key = [...env.store.keys()].find((k) => k.startsWith("h:"));
  assert.equal(env.store.get(key).metadata.bot, true);

  ctx = makeCtx();
  const head = await worker.fetch(req("/t/ab12cd34", { method: "HEAD", headers: { "user-agent": BROWSER } }), env, ctx);
  await ctx.done();
  assert.equal(head.status, 302);
  assert.equal([...env.store.keys()].filter((k) => k.startsWith("h:")).length, 1);
});

test("l'API refuse tout appel sans la clé", async () => {
  const env = makeEnv();
  const noKey = await worker.fetch(req("/api/hits"), env, makeCtx());
  assert.equal(noKey.status, 401);
  const wrong = await worker.fetch(req("/api/hits", { headers: { authorization: "Bearer mauvaise-cle" } }), env, makeCtx());
  assert.equal(wrong.status, 401);
  const noSecret = await worker.fetch(req("/api/hits", { headers: auth }), makeEnv({ API_KEY: "" }), makeCtx());
  assert.equal(noSecret.status, 401);
});

test("la déclaration refuse les cibles non HTTPS et les jetons réservés", async () => {
  const env = makeEnv();
  const res = await register(env, [
    { ...TRACKER, token: "bad00001", target: "http://example.com" },
    { ...TRACKER, token: "bad00002", target: "javascript:alert(1)" },
    { ...TRACKER, token: "linkedin" },
    TRACKER,
  ]);
  const body = await res.json();
  assert.equal(body.registered, 1);
  assert.equal(body.ok, false);
  assert.deepEqual([...env.store.keys()], ["t:ab12cd34"]);
});

test("/api/hits renvoie les clics dans l'ordre et reprend après un curseur", async () => {
  const env = makeEnv();
  await register(env);
  for (let i = 0; i < 3; i++) {
    const ctx = makeCtx();
    await worker.fetch(req("/t/ab12cd34", { headers: { "user-agent": BROWSER } }), env, ctx);
    await ctx.done();
    await new Promise((r) => setTimeout(r, 3));
  }
  const all = await (await worker.fetch(req("/api/hits", { headers: auth }), env, makeCtx())).json();
  assert.equal(all.hits.length, 3);
  assert.deepEqual(all.hits.map((h) => h.key), [...all.hits.map((h) => h.key)].sort());

  const after = encodeURIComponent(all.hits[0].key);
  const rest = await (await worker.fetch(req(`/api/hits?after=${after}`, { headers: auth }), env, makeCtx())).json();
  assert.equal(rest.hits.length, 2);
});

test("mise en forme : appareil lisible, HTML échappé, robot annoncé", () => {
  assert.equal(describeDevice(BROWSER), "Windows · Chrome");
  assert.equal(describeDevice(""), "Appareil inconnu");
  const hit = { ts: "2026-11-10T20:00:00Z", ua: BROWSER, city: "Paris", country: "FR", bot: false };
  const text = formatAlert({ ...TRACKER, company: "A <b>& B" }, hit);
  assert.match(text, /A &lt;b&gt;&amp; B/);
  assert.match(text, /Paris, FR/);
  assert.match(formatAlert(TRACKER, { ...hit, bot: true }), /robot/);
});
