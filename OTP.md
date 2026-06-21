# OTP Automation Setup

This documents the Qommunity OTP automation work done on 2026-06-21.

## Goal

Automate Qommunity email OTP login so `tennis_booker.py` can refresh `qommunity_auth.json` from cron without manual OTP entry.

The intended production flow is:

```text
tennis_booker.py --login email --otp-source worker
  -> Qommunity sends OTP email to Gmail
  -> Gmail forwards matching OTP email to qommunity-otp@tiufamily.com
  -> Cloudflare Email Routing sends that address to the qommunity-otp Worker
  -> Worker extracts OTP from the raw email
  -> Worker sends a Telegram notification via TiuFamBot
  -> Worker stores latest OTP in Cloudflare KV
  -> tennis_booker.py polls Worker /otp with a bearer secret
  -> tennis_booker.py exchanges OTP for Qommunity auth and writes qommunity_auth.json
```

Telegram is only for visibility. The script reads OTPs from the Worker endpoint, not from Telegram. This avoids relying on Telegram bot update behavior for messages the bot itself sends.

## Current State

Cloudflare account:

```text
account id: b1b1e1a7a816d80687231217f7bc6551
zone: tiufamily.com
zone id: 85030f98f2498c80eebf5bc3f1df3a14
```

Cloudflare Worker:

```text
name: qommunity-otp
workers.dev URL: https://qommunity-otp.tiny-tree-cd6f.workers.dev
health URL: https://qommunity-otp.tiny-tree-cd6f.workers.dev/health
OTP URL: https://qommunity-otp.tiny-tree-cd6f.workers.dev/otp
```

Cloudflare KV:

```text
binding: OTP_KV
namespace id: e98943b4d7824f0ca9fe7649f8dba500
key used by Worker: latest
```

Telegram:

```text
bot: TiuFamBot
chat id: 262434816
bot token file: ~/.telegram
```

Local Worker read secret:

```text
~/.qommunity_otp_secret
```

Local Cloudflare token:

```text
~/.cloudflare
```

Local ignored auth config:

```text
auth_config.json
```

The current local `auth_config.json` points `tennis_booker.py` at:

```text
https://qommunity-otp.tiny-tree-cd6f.workers.dev/otp
```

## Cloudflare Resources Created

Created KV namespace:

```bash
CLOUDFLARE_API_TOKEN="$(cat ~/.cloudflare)" npx wrangler kv namespace create OTP_KV
```

Result:

```text
OTP_KV id: e98943b4d7824f0ca9fe7649f8dba500
```

Uploaded Worker secrets:

```bash
cat ~/.telegram | CLOUDFLARE_API_TOKEN="$(cat ~/.cloudflare)" npx wrangler secret put TELEGRAM_TOKEN
cat ~/.qommunity_otp_secret | CLOUDFLARE_API_TOKEN="$(cat ~/.cloudflare)" npx wrangler secret put OTP_READ_SECRET
```

Deployed Worker:

```bash
cd /Users/tiuweehan/projects/qommunity/cloudflare
CLOUDFLARE_API_TOKEN="$(cat ~/.cloudflare)" npx wrangler deploy
```

Current local `cloudflare/wrangler.toml` is ignored by git and contains deployment-specific values. Important settings:

```toml
name = "qommunity-otp"
main = "qommunity-otp-worker.js"
compatibility_date = "2026-06-21"
workers_dev = true

[[kv_namespaces]]
binding = "OTP_KV"
id = "e98943b4d7824f0ca9fe7649f8dba500"

[vars]
TELEGRAM_CHAT_ID = "262434816"
OTP_REGEX = "\\b\\d{4,8}\\b"
OTP_TTL_SECONDS = "600"
TELEGRAM_NOTIFY_NO_OTP = "false"
```

## Email Routing Rule

Created Cloudflare Email Routing rule:

```text
rule id: c32455cc443c43b1971448ae56ca0f10
name: Qommunity OTP to Telegram Worker
enabled: true
priority: 0
matcher: to == qommunity-otp@tiufamily.com
action: worker qommunity-otp
```

There is also a disabled catch-all drop rule:

```text
rule id: 80802c66334e43ed88e9cbb0c03f2603
matcher: all
action: drop
enabled: false
```

## Worker Behavior

Worker source:

```text
cloudflare/qommunity-otp-worker.js
```

Handlers:

```text
fetch(request, env)
email(message, env)
```

Email handler behavior:

```text
1. Reads raw incoming email.
2. Extracts first OTP matching OTP_REGEX.
3. Writes JSON record to KV key "latest" with TTL OTP_TTL_SECONDS.
4. Sends Telegram message to TELEGRAM_CHAT_ID using TELEGRAM_TOKEN.
5. Optionally forwards to FORWARD_TO if configured.
```

KV record shape:

```json
{
  "otp": "123456",
  "receivedAt": "2026-06-21T12:59:00.000Z",
  "receivedAtEpoch": 1782046740,
  "from": "sender@example.com",
  "to": "qommunity-otp@tiufamily.com",
  "subject": "..."
}
```

Fetch endpoints:

```text
GET /health
```

Returns public health status:

```json
{ "ok": true }
```

```text
GET /otp?after=<epoch>&contact=<email>&mode=email
Authorization: Bearer <contents of ~/.qommunity_otp_secret>
```

Returns:

```text
200 with latest OTP JSON if present and newer than after
202 {"status":"pending"} if no current OTP is available
401 if bearer token is missing or wrong
```

## Custom Domain Cleanup

A custom Worker hostname under `tiufamily.com` was briefly created. It was then removed from Cloudflare Workers custom domains after switching to `workers.dev`.

Verification after removal:

```text
GET old custom Worker hostname -> Cloudflare 1016
GET https://qommunity-otp.tiny-tree-cd6f.workers.dev/health -> 200 {"ok": true}
```

Current Cloudflare checks showed:

```text
Workers custom domains: 0
Workers routes: 0
```

The only remaining `tiufamily.com` usage is the email address:

```text
qommunity-otp@tiufamily.com
```

That is intentional because Cloudflare Email Routing must receive mail at your domain.

## tennis_booker.py Changes

`tennis_booker.py` supports login modes:

```bash
~/venv/bin/python tennis_booker.py --login
~/venv/bin/python tennis_booker.py --login email
~/venv/bin/python tennis_booker.py --login mobile
```

Email is the default.

It also supports OTP sources:

```bash
--otp-source prompt
--otp-source worker
```

Important flags:

```text
--otp-worker-url
--otp-secret-file
--otp-secret
--otp-timeout-seconds
--otp-poll-interval
--otp-regex
```

Current ignored `auth_config.json` contains:

```json
{
  "auth": {
    "client_id": "fbc7149c8b3244ddb754c090918b7621.mtwpublicapp.com.ibase",
    "email": {
      "contactType": "2",
      "contact": "deantiu56@gmail.com"
    },
    "mobile": {
      "contactType": "1",
      "contact": "85331217",
      "mobileCountryCode": "+65"
    }
  },
  "otp": {
    "source": "worker",
    "worker_url": "https://qommunity-otp.tiny-tree-cd6f.workers.dev/otp",
    "secret_file": "~/.qommunity_otp_secret",
    "timeout_seconds": 180,
    "poll_interval": 2,
    "regex": "\\b\\d{4,8}\\b"
  }
}
```

This file is ignored by git.

## Validation Already Done

Verified Cloudflare token:

```text
GET /user/tokens/verify -> success true
```

Verified Cloudflare resources:

```text
GET /zones?name=tiufamily.com -> tiufamily.com found
GET /accounts -> account found
GET /workers/scripts -> qommunity-otp found
GET /email/routing/rules -> Qommunity OTP rule found
```

Verified Worker health:

```bash
curl https://qommunity-otp.tiny-tree-cd6f.workers.dev/health
```

Expected:

```json
{ "ok": true }
```

Verified protected `/otp` endpoint:

```bash
secret="$(cat ~/.qommunity_otp_secret)"
curl -H "Authorization: Bearer $secret" \
  "https://qommunity-otp.tiny-tree-cd6f.workers.dev/otp?after=0"
```

Expected before any OTP email:

```json
{ "status": "pending" }
```

## Remaining Manual Gmail Setup

Gmail must forward Qommunity OTP emails to:

```text
qommunity-otp@tiufamily.com
```

Recommended Gmail filter:

```text
from: Qommunity sender address, once known
subject/body: OTP or verification keyword
action: forward to qommunity-otp@tiufamily.com
```

Keep the filter narrow. Do not forward all email.

If Gmail requires forwarding address verification:

```text
1. Add qommunity-otp@tiufamily.com as forwarding address.
2. Gmail sends verification email.
3. Cloudflare Worker receives it and should Telegram it.
4. Use the verification code/link from Telegram to confirm forwarding.
```

## End-To-End Test

After Gmail forwarding is active:

```bash
cd /Users/tiuweehan/projects/qommunity
~/venv/bin/python tennis_booker.py --login email --otp-source worker
```

Expected behavior:

```text
1. Script requests Qommunity email OTP.
2. Gmail receives OTP.
3. Gmail forwards it to qommunity-otp@tiufamily.com.
4. Worker sends Telegram message.
5. Script logs "OTP received".
6. Script writes qommunity_auth.json.
```

## Cron Example

Refresh auth every Monday and Thursday at 08:00:

```cron
0 8 * * 1,4 cd /Users/tiuweehan/projects/qommunity && /Users/tiuweehan/venv/bin/python tennis_booker.py --login email --otp-source worker --log-file tennis_booker.log
```

## Important Security Notes

Do not commit:

```text
~/.cloudflare
~/.telegram
~/.qommunity_otp_secret
auth_config.json
qommunity_auth.json
cloudflare/wrangler.toml
```

The `/otp` endpoint is public internet-facing but bearer-protected. Anyone without the secret receives `401`.

The Telegram bot token and OTP read secret are stored as Cloudflare Worker secrets, not Wrangler plain vars.

## Useful Commands

List Email Routing rules:

```bash
~/venv/bin/python - <<'PY'
from pathlib import Path
import requests, json
cf = Path("~/.cloudflare").expanduser().read_text().strip()
zone = "85030f98f2498c80eebf5bc3f1df3a14"
h = {"Authorization": f"Bearer {cf}", "Content-Type": "application/json"}
r = requests.get(f"https://api.cloudflare.com/client/v4/zones/{zone}/email/routing/rules", headers=h)
print(json.dumps(r.json(), indent=2))
PY
```

Redeploy Worker:

```bash
cd /Users/tiuweehan/projects/qommunity/cloudflare
CLOUDFLARE_API_TOKEN="$(cat ~/.cloudflare)" npx wrangler deploy
```

Tail Worker logs:

```bash
cd /Users/tiuweehan/projects/qommunity/cloudflare
CLOUDFLARE_API_TOKEN="$(cat ~/.cloudflare)" npx wrangler tail qommunity-otp
```

Check Worker URL:

```bash
curl https://qommunity-otp.tiny-tree-cd6f.workers.dev/health
```

Check latest OTP:

```bash
secret="$(cat ~/.qommunity_otp_secret)"
curl -H "Authorization: Bearer $secret" \
  "https://qommunity-otp.tiny-tree-cd6f.workers.dev/otp?after=0"
```
