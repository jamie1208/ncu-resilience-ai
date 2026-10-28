# infra

- `docker/`：docker-compose.yml（P0-T04）、docker-compose.prod.yml（P5-T06）
- `migrations/versions/`：Alembic migrations（P0-T05 起）
- `monitoring/`：Prometheus 設定與告警規則、Grafana provisioning 與 dashboard（P5-T04；見 monitoring/README.md）
- `caddy/`：反向代理與 TLS（P5-T06）
- `scripts/`：backup.sh、restore.sh（P5-T05）
