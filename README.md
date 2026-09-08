# Naoya's Middleman Service

A Telegram middleman and escrow bot for managing deals between buyers and sellers. It includes deal tracking, payment verification, dynamic UPI QR generation, support tickets, reviews, audit logging, and SQLite persistence.

## Features

- Buyer and seller deal creation and confirmation
- Middleman assignment and deal status tracking
- Payment proof submission and admin verification
- Dynamic UPI QR codes for payment collection
- Hold, release, cancel, and dispute workflows
- Group chat deal locking and automatic middleman promotion
- Support tickets, vouches, and audit logs
- Polling mode for local hosting
- Flask webhook endpoint for platforms such as Vercel

## Requirements

- Python 3.10 through 3.14
- A Telegram bot created through [@BotFather](https://t.me/BotFather)
- Telegram group/channel IDs for logging and support
- A configured UPI ID if UPI payments are enabled

Install the dependencies with:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

## Configuration

Configuration is currently defined in the `Config` class in `app.py`. Before running the bot, replace the placeholder or obfuscated values with your own values for:

- `BOT_TOKEN`
- `SUPER_OWNER_IDS`, `OWNER_IDS`, and `ADMIN_IDS`
- `DEAL_LOG_CHANNEL_ID`, `PAYMENT_LOG_CHANNEL_ID`, and `SUPPORT_GROUP_ID`
- `UPI_ID` and `UPI_PAYEE_NAME`
- Fee and currency settings where needed

The binary encoding in the file is only obfuscation, not encryption. Never commit bot tokens, payment credentials, or private Telegram IDs to a public repository. Rotate any credentials that have already been exposed and move secrets to environment variables before production use.

## Run locally

Start the bot in Telegram polling mode:

```powershell
python app.py
```

The bot initializes `naoya_mm.db` in the project directory. When the `VERCEL` environment variable is set, it uses `/tmp/naoya_mm.db` because Vercel's filesystem is not persistent.

Useful user commands include:

- `/start` or `/s` - open the dashboard
- `/help` - show workflow help
- `/myid` - show the user's middleman ID
- `/status` - show the current deal status
- `/deals` - list active deals
- `/hold` - submit or manage a payment hold
- `/release` - release funds after confirmation
- `/cancel` - cancel the current step

## Webhook hosting

The module exposes these Flask endpoints:

- `GET /` - health check
- `POST /webhook` - Telegram webhook receiver

For production webhook hosting, set `TELEGRAM_WEBHOOK_SECRET` and configure Telegram to send updates to your public `/webhook` URL. The hosting platform must expose the Flask `app` object and provide the required environment variables securely.

## Project files

```text
app.py            Telegram bot and Flask webhook implementation
requirements.txt  Python dependencies
naoya_mm.db       Local SQLite database created at runtime
```

## Disclaimer

This software handles payment-related workflows. Review permissions, authentication, webhook security, persistence, and operational recovery procedures carefully before using it with real funds.
