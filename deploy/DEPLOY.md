# Deploying a client's Swarm to a VPS

One client = one cheap VPS ($5–15/mo, Ubuntu). Isolation is a feature.

## 1. Server prep (once per VPS)

```bash
sudo apt update && sudo apt install -y python3 python3-pip rsync
sudo useradd -m swarm
```

## 2. Ship the code (from your Mac, each release)

```bash
rsync -av --exclude .git --exclude __pycache__ --exclude .staging \
      --exclude 'clients/*' ./ swarm@YOUR_VPS:/home/swarm/app/
rsync -av clients/CLIENT_NAME swarm@YOUR_VPS:/home/swarm/app/clients/
ssh swarm@YOUR_VPS 'cd ~/app && pip install -r requirements.txt'
```

## 3. Configure on the server

Create `/home/swarm/app/.env` **on the server** (never commit it):

```
SWARM_BASE_URL=...      # provider
SWARM_API_KEY=...       # provider key
SWARM_MODEL=...
SWARM_SERVER_TOKEN=<long random string>   # REQUIRED in production
SWARM_RATE_LIMIT=60
```

## 4. Run as services (systemd)

`/etc/systemd/system/swarm-api.service`:
```ini
[Unit]
Description=Swarm API (CLIENT_NAME)
After=network.target
[Service]
User=swarm
WorkingDirectory=/home/swarm/app
EnvironmentFile=/home/swarm/app/.env
ExecStart=/usr/bin/python3 run.py --client CLIENT_NAME --serve 8080
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/swarm-worker.service`: same, with
`ExecStart=/usr/bin/python3 run.py --client CLIENT_NAME --work`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now swarm-api swarm-worker
```

## 5. Verify

```bash
curl http://localhost:8080/health
curl -X POST http://localhost:8080/handle \
  -H "Authorization: Bearer $SWARM_SERVER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'
```

Put nginx/caddy with HTTPS in front before exposing publicly. Check
`python3 run.py --client CLIENT_NAME --status` for the owner digest and
LLM cost counters. Back up `clients/CLIENT_NAME/db/` nightly (it's one file).
