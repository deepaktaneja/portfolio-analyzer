#!/usr/bin/env bash
# Stage 10 container entrypoint: migrate/provision then launch Streamlit.
set -euo pipefail

echo "[entrypoint] applying migrations & provisioning DBs"
python -m scripts.migrate || echo "[entrypoint] warning: migration reported issues (continuing)"

echo "[entrypoint] starting Streamlit on :8501"
exec streamlit run app.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
