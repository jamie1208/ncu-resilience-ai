# infra

- `docker/`：docker-compose.yml（P0-T04）、docker-compose.local.yml（P5-T06 本機常駐運行；見 docs/runbooks/local-operation.md）
- `migrations/versions/`：Alembic migrations（P0-T05 起）
- `monitoring/`：Prometheus 設定與告警規則、Grafana provisioning 與 dashboard（P5-T04；見 monitoring/README.md）
- `scripts/`：backup.sh、restore.sh（P5-T05）
