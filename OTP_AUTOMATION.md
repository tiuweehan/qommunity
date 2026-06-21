# Qommunity OTP Automation

This sets up email OTP delivery so `tennis_booker.py` can refresh auth without manual input.

## Architecture

```text
Qommunity OTP email
  -> Gmail forwarding filter
  -> qommunity-otp@tiufamily.com
  -> Cloudflare Email Routing Worker
  -> Telegram notification
  -> secret /otp endpoint polled by tennis_booker.py
```

Telegram is used for visibility. The script reads from the Worker endpoint, not from Telegram, because Telegram Bot API does not reliably let a bot read its own outgoing messages.

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
    "timeout_seconds": 180,
    "poll_interval": 2,
    "regex": "\\b\\d{4,8}\\b"
  }
}
```

## Telegram

1. Create a bot via `@BotFather`.
2. Save the bot token for `wrangler secret put TELEGRAM_TOKEN`.
3. Get your chat ID by messaging the bot, then calling:

```bash
curl "https://api.telegram.org/bot<token>/getUpdates"
```

Use that chat ID as `TELEGRAM_CHAT_ID`.

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
wrangler secret put TELEGRAM_TOKEN
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

## Cloudflare Email Routing

For `tiufamily.com`:

1. Enable Email Routing.
2. Add a custom address such as `qommunity-otp@tiufamily.com`.
3. Route that address to the `qommunity-otp` Worker.

## Gmail Forwarding

1. Gmail Settings -> Forwarding and POP/IMAP -> Add forwarding address.
2. Add `qommunity-otp@tiufamily.com`.
3. Complete Gmail's verification email.
4. Create a filter for Qommunity OTP messages.
5. Choose `Forward it` to `qommunity-otp@tiufamily.com`.

Keep the filter narrow so unrelated email does not get sent to the Worker.

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
