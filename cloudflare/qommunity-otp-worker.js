const DEFAULT_OTP_REGEX = "\\b\\d{4,8}\\b";

function json(data, init = {}) {
  return new Response(JSON.stringify(data, null, 2), {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init.headers || {}),
    },
  });
}

function authorized(request, env) {
  const expected = env.OTP_READ_SECRET || "";
  const header = request.headers.get("authorization") || "";
  return expected && header === `Bearer ${expected}`;
}

function extractOtp(text, env) {
  const pattern = new RegExp(env.OTP_REGEX || DEFAULT_OTP_REGEX);
  const match = text.match(pattern);
  return match ? match[0] : "";
}

async function sendTelegram(record, env) {
  if (!env.TELEGRAM_TOKEN || !env.TELEGRAM_CHAT_ID) {
    return;
  }
  const message = [
    "Qommunity OTP",
    `OTP: ${record.otp}`,
    `From: ${record.from || ""}`,
    `To: ${record.to || ""}`,
    `Subject: ${record.subject || ""}`,
    `Received: ${record.receivedAt}`,
  ].join("\n");

  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text: message,
      disable_web_page_preview: true,
    }),
  });
}

async function handleEmail(message, env) {
  const raw = await new Response(message.raw).text();
  const otp = extractOtp(raw, env);
  const subject = message.headers.get("subject") || "";
  const record = {
    otp,
    receivedAt: new Date().toISOString(),
    receivedAtEpoch: Math.floor(Date.now() / 1000),
    from: message.from,
    to: message.to,
    subject,
  };

  if (otp) {
    const ttl = Number(env.OTP_TTL_SECONDS || 600);
    await env.OTP_KV.put("latest", JSON.stringify(record), { expirationTtl: ttl });
    await sendTelegram(record, env);
  } else if (env.TELEGRAM_NOTIFY_NO_OTP === "true") {
    await sendTelegram({ ...record, otp: "not found" }, env);
  }

  if (env.FORWARD_TO) {
    await message.forward(env.FORWARD_TO);
  }
}

async function handleFetch(request, env) {
  const url = new URL(request.url);
  if (url.pathname === "/health") {
    return json({ ok: true });
  }
  if (url.pathname !== "/otp") {
    return json({ error: "not found" }, { status: 404 });
  }
  if (!authorized(request, env)) {
    return json({ error: "unauthorized" }, { status: 401 });
  }

  const latest = await env.OTP_KV.get("latest", "json");
  if (!latest || !latest.otp) {
    return json({ status: "pending" }, { status: 202 });
  }

  const after = Number(url.searchParams.get("after") || 0);
  if (after && Number(latest.receivedAtEpoch || 0) < after) {
    return json({ status: "pending" }, { status: 202 });
  }

  return json(latest);
}

export default {
  async fetch(request, env) {
    return handleFetch(request, env);
  },
  async email(message, env) {
    return handleEmail(message, env);
  },
};
