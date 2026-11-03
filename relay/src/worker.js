import { DurableObject } from "cloudflare:workers";

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/ws" || url.pathname === "/agent") {
      return env.RELAY.get(env.RELAY.idFromName(env.ROOM)).fetch(req);
    }
    if (url.pathname === "/download") {
      return Response.redirect(env.DOWNLOAD_URL, 302);
    }
    if (url.pathname === "/") {
      const page = await env.ASSETS.fetch(req);
      const res = new Response(page.body, page);
      res.headers.set("Cache-Control", "no-store");
      return res;
    }
    return new Response("Not found", { status: 404 });
  },
};

export class Relay extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  async fetch(req) {
    if (req.headers.get("Upgrade") !== "websocket") {
      return new Response("Expected a WebSocket", { status: 426 });
    }
    const url = new URL(req.url);
    return url.pathname === "/agent" ? this.joinAgent(req) : this.joinViewer(req);
  }

  async joinAgent(req) {
    const key = (req.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
    if (key.length < 32) return new Response("Missing key", { status: 401 });
    const hash = await sha256(key);
    const saved = await this.ctx.storage.get("agent");
    if (saved && saved !== hash) return new Response("This link belongs to another laptop", { status: 403 });
    if (!saved) await this.ctx.storage.put("agent", hash);

    const [client, server] = Object.values(new WebSocketPair());
    for (const old of this.ctx.getWebSockets("agent")) {
      try { old.close(4000, "replaced"); } catch (_) {}
    }
    this.ctx.acceptWebSocket(server, ["agent"]);
    server.serializeAttachment({ role: "agent" });
    for (const viewer of this.ctx.getWebSockets("viewer")) {
      const { id, ip } = viewer.deserializeAttachment();
      server.send(JSON.stringify({ t: "open", id, ip }));
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  joinViewer(req) {
    if (this.ctx.getWebSockets("viewer").length >= 50) {
      return new Response("Too many people on this page", { status: 503 });
    }
    const [client, server] = Object.values(new WebSocketPair());
    const id = crypto.randomUUID();
    const ip = req.headers.get("CF-Connecting-IP") || "";
    this.ctx.acceptWebSocket(server, ["viewer", id]);
    server.serializeAttachment({ role: "viewer", id, ip });
    const agent = this.agent();
    if (agent) agent.send(JSON.stringify({ t: "open", id, ip }));
    else server.send(JSON.stringify({ t: "laptop", online: false }));
    return new Response(null, { status: 101, webSocket: client });
  }

  agent(except) {
    return this.ctx.getWebSockets("agent").find((ws) => ws !== except);
  }

  webSocketMessage(ws, message) {
    if (typeof message !== "string" || message.length > 16384) return;
    const me = ws.deserializeAttachment() || {};
    if (me.role === "agent") {
      let msg;
      try { msg = JSON.parse(message); } catch (_) { return; }
      const target = msg.t === "send" && msg.id ? this.ctx.getWebSockets(msg.id)[0] : null;
      if (target) target.send(JSON.stringify(msg.data));
      return;
    }
    const agent = this.agent();
    if (agent) agent.send(JSON.stringify({ t: "msg", id: me.id, data: message }));
  }

  webSocketClose(ws) {
    try { ws.close(1000, "bye"); } catch (_) {}
    const me = ws.deserializeAttachment() || {};
    if (me.role === "agent") {
      if (this.agent(ws)) return;
      for (const viewer of this.ctx.getWebSockets("viewer")) {
        try { viewer.send(JSON.stringify({ t: "laptop", online: false })); } catch (_) {}
      }
      return;
    }
    const agent = this.agent();
    if (agent && me.id) agent.send(JSON.stringify({ t: "close", id: me.id }));
  }

  webSocketError(ws) {
    this.webSocketClose(ws);
  }
}
