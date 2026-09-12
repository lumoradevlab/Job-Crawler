#!/usr/bin/env bash
# Put the bot on a fresh Debian or Ubuntu box. Idempotent: running it again
# upgrades the code in place and leaves the subscriber file alone.
#
#   sudo ./deploy/install.sh
#
# Afterwards, put the token in /opt/jobcrawler/.env and start the service.

set -euo pipefail

APP=/opt/jobcrawler
STATE=/var/lib/jobcrawler
USER=jobcrawler
REPO=https://github.com/lumoradevlab/Job-Crawler.git

[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git

# A service account with no login and no home: the bot needs a network
# socket and one state directory, and nothing a shell would give it.
id -u "$USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER"

if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --quiet origin
  git -C "$APP" reset --hard --quiet origin/main
else
  git clone --quiet "$REPO" "$APP"
fi

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet "$APP"

# The subscriber file is the only thing here that cannot be rebuilt from the
# repo: it is everyone's roles, countries and history. It lives outside the
# checkout so that upgrading the code cannot touch it.
install -d -o "$USER" -g "$USER" -m 750 "$STATE"
[ -f "$STATE/subscribers.json" ] || { echo '{}' > "$STATE/subscribers.json"; }
chown "$USER:$USER" "$STATE/subscribers.json"
chmod 600 "$STATE/subscribers.json"

if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  echo "→ put your token in $APP/.env"
fi
chown root:"$USER" "$APP/.env"
chmod 640 "$APP/.env"

install -m 644 "$APP/deploy/jobcrawler-bot.service" /etc/systemd/system/
systemctl daemon-reload

cat <<'DONE'

Installed. Two steps left:

  1. sudo nano /opt/jobcrawler/.env        # TELEGRAM_BOT_TOKEN=...
  2. sudo systemctl enable --now jobcrawler-bot

Then watch it come up:

     journalctl -u jobcrawler-bot -f

You are looking for a line naming your bot and the subscriber count, then
"watching · polling every 10s". Press Start in Telegram and the reply
should arrive within ten seconds.
DONE
