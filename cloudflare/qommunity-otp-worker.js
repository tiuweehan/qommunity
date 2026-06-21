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
  const preferredPatterns = [
    /\byour\s+otp\s+is\s+(\d{4,8})\b/i,
    /\botp\s*(?:is|:|-)?\s*(\d{4,8})\b/i,
    /\bcode\s*(?:is|:|-)?\s*(\d{4,8})\b/i,
  ];
  for (const preferredPattern of preferredPatterns) {
    const preferredMatch = text.match(preferredPattern);
    if (preferredMatch) {
      return preferredMatch[1];
    }
  }

  const pattern = new RegExp(env.OTP_REGEX || DEFAULT_OTP_REGEX);
  const match = text.match(pattern);
  return match ? match[0] : "";
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

  if (env.STORE_RAW_EMAIL === "true") {
    try {
      const ttl = Number(env.RAW_EMAIL_TTL_SECONDS || 600);
      await env.OTP_KV.put("latest_raw", raw.slice(0, Number(env.RAW_EMAIL_MAX_BYTES || 100000)), {
        expirationTtl: ttl,
      });
      record.rawStored = true;
    } catch (error) {
      record.rawStored = false;
      record.rawError = error instanceof Error ? error.message : String(error);
    }
  }

  if (otp) {
    const ttl = Number(env.OTP_TTL_SECONDS || 600);
    await env.OTP_KV.put("latest", JSON.stringify(record), { expirationTtl: ttl });
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
  if (url.pathname === "/raw") {
    if (!authorized(request, env)) {
      return json({ error: "unauthorized" }, { status: 401 });
    }
    const raw = await env.OTP_KV.get("latest_raw");
    if (!raw) {
      return json({ status: "pending" }, { status: 202 });
    }
    return new Response(raw, {
      headers: {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
      },
    });
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
