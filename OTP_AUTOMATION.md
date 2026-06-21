# Qommunity OTP Automation

This sets up email OTP delivery so `tennis_booker.py` can refresh auth without manual input.

## Architecture

```text
Qommunity OTP email
  -> Gmail
  -> Apple custom domain address at tiuweehan.com
  -> Apple Mail rule for subject containing "OTP for Qommunity"
  -> qommunity-otp@tiufamily.com
  -> Cloudflare Email Routing Worker
  -> Cloudflare KV
  -> secret /otp endpoint polled by tennis_booker.py
```

## Local Files

Local secret:

```text
~/.qommunity_otp_secret
```

This is the bearer token used by `tennis_booker.py` to read from the Worker. It must match the Worker secret `OTP_READ_SECRET`.

Ignored local config:

```json
{
  "otp": {
    "source": "worker",
    "worker_url": "https://qommunity-otp.<your-subdomain>.workers.dev/otp",
    "secret_file": "~/.qommunity_otp_secret",
    "timeout_seconds": 300,
    "poll_interval": 2,
    "regex": "\\b\\d{4,8}\\b"
  }
}
```

## Cloudflare Worker

Copy the example:

```bash
cp cloudflare/wrangler.qommunity-otp.example.toml cloudflare/wrangler.toml
```

Create KV:

```bash
cd cloudflare
wrangler kv namespace create OTP_KV
```

Put the returned KV namespace id into `wrangler.toml`.

Set secrets:

```bash
wrangler secret put OTP_READ_SECRET
```

For `OTP_READ_SECRET`, use the content of:

```bash
cat ~/.qommunity_otp_secret
```

Deploy:

```bash
wrangler deploy
```

Health check:

```bash
curl https://qommunity-otp.<your-subdomain>.workers.dev/health
```

Fetch latest OTP:

```bash
secret="$(cat ~/.qommunity_otp_secret)"
curl -H "Authorization: Bearer $secret" \
  "https://qommunity-otp.<your-subdomain>.workers.dev/otp?after=0"
```

If `STORE_RAW_EMAIL = "true"` is enabled, fetch the latest raw forwarded email:

```bash
secret="$(cat ~/.qommunity_otp_secret)"
curl -H "Authorization: Bearer $secret" \
  "https://qommunity-otp.<your-subdomain>.workers.dev/raw"
```

The raw endpoint is mainly for Gmail forwarding verification and debugging. Keep its TTL short.

## Cloudflare Email Routing

For `tiufamily.com`:

1. Enable Email Routing.
2. Add a custom address such as `qommunity-otp@tiufamily.com`.
3. Route that address to the `qommunity-otp` Worker.

## Email Forwarding

Current forwarding chain:

```text
Gmail -> Apple custom domain mail at tiuweehan.com -> Apple Mail rule -> qommunity-otp@tiufamily.com
```

Apple Mail introduces an extra forwarding hop, so OTP delivery can take around a minute. The local `auth_config.json` uses a 300 second Worker polling timeout to tolerate that delay.

Apple Mail rule:

```text
if subject contains "OTP for Qommunity"
then forward to qommunity-otp@tiufamily.com
```

The Cloudflare Worker only receives the message after Apple Mail processes that rule.

## Gmail Forwarding Verification

1. Gmail Settings -> Forwarding and POP/IMAP -> Add forwarding address.
2. Add the Apple custom domain address that receives Qommunity OTP email.
3. Complete Gmail's verification email.
4. Create a filter for Qommunity OTP messages.
5. Choose `Forward it` to the Apple custom domain address.

Keep the Gmail and Apple Mail rules narrow so unrelated email does not get sent to the Worker.

## Test

Once deployed and Gmail forwarding is active:

```bash
~/venv/bin/python tennis_booker.py --login email --otp-source worker
```

The script will:

1. request the OTP from Qommunity,
2. poll the Worker `/otp` endpoint,
3. extract the OTP,
4. save `qommunity_auth.json`.

## Cron

Example: refresh auth every Monday and Thursday at 08:00 Singapore time.

```cron
0 8 * * 1,4 cd /Users/tiuweehan/projects/qommunity && /Users/tiuweehan/venv/bin/python tennis_booker.py --login email --otp-source worker --log-file tennis_booker.log
```
