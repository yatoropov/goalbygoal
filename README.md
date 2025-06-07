# GoalByGoal

Telegram bot that helps parents assign simple daily tasks to their children and track completion via photos.

## Features

- **Role selection** – user chooses to act as a parent or child when interacting with the bot.
- **Invite codes** – parents generate a 6 character code that children use to join the family team.
- **Task management** – parents select tasks from a predefined list and assign them to all connected children.
- **Photo confirmation** – children send a photo as proof of task completion. The bot checks the EXIF timestamp to ensure the photo was taken today.
- **Firestore storage** – user profiles, invites and task data are stored in Google Cloud Firestore.
- **Webhook support** – the bot is designed to run with a webhook endpoint for Telegram updates.

## Requirements

- Python 3.11
- Telegram bot token
- Google Cloud Firestore project and credentials

Required Python packages are listed in `requirements.txt`.

## Configuration

Create a `.env` file using `.env.example` as a template:

```bash
cp .env.example .env
```

Set the following environment variables:

- `TELEGRAM_TOKEN` – token of your Telegram bot
- `WEBHOOK_URL` – base URL where Telegram will send webhooks
- `GOOGLE_CLOUD_PROJECT` – Firestore project ID
- `GOOGLE_APPLICATION_CREDENTIALS` – path to service account JSON (if not using other auth methods)

## Running locally

Install dependencies and start the bot:

```bash
pip install -r requirements.txt
python main.py
```

The bot will start an aiohttp server and register a webhook at `WEBHOOK_URL`.

## Docker

To run the bot in Docker:

```bash
docker build -t goalbygoal .
docker run --env-file .env -p 8080:8080 goalbygoal
```

## Billing bot

The repository also contains `billing.py` which replicates the old Google Apps
Script for generating invoices from Google Docs templates.  It uses Google
Sheets and Drive APIs and can be deployed on Cloud Run in the same way as the
main bot.  Configure the additional environment variables in `.env.example` and
start the service:

```bash
pip install -r requirements.txt
python billing.py
```

## Health check

The container exposes `/health` endpoint which returns `OK` and can be used for monitoring.

## Нові можливості

- Завдання можна призначати окремо кожній дитині
- Батьки можуть додавати або видаляти дітей
- Дитина після приєднання може вказати своє ім'я

## Requirements
   python:3.11.8-slim-bullseye
   aiogram==2.25.2
   python-dotenv
   pillow
   google-cloud-firestore
   aiohttp

