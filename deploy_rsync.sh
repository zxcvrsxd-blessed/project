#!/usr/bin/env bash
set -euo pipefail

rsync -avz \
  --exclude 'venv/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.env' \
  --exclude 'instance/*.db' \
  ./ user@37.233.83.18:/home/user/siteflask/
