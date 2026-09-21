# Shared Services Registry

Краткий человекочитаемый реестр. Machine-readable source of truth:
`docs/services.yaml`.

| Service | Docker DNS / internal | Published host port | Lifecycle |
|---|---|---:|---|
| `shared-vlm` | `http://shared-vlm:8000/v1` | `8000` | shared, profile `ai-vlm` |
| `shared-embedding` | `http://shared-embedding:8000` | `8001` | shared, profile `ai-embedding` |
| `open-webui` | `http://open-webui:8080` | `3000` | shared optional, profile `ai-ui` |
| `n8n` | `http://n8n:5678` | `5678` | shared |
| `rabbitmq` | `rabbitmq:5672` | `5672` | shared |
| RabbitMQ Management | — | `15672` | shared admin UI |
| `n8n-db` | `n8n-db:5432` | не публикуется | infrastructure-private |

## Правило доступа

На том же Docker host application SHOULD использовать `ai-shared` и Docker DNS.

С другого компьютера или application server использовать:

```text
http://<shared-host>:8000/v1
http://<shared-host>:8001
http://<shared-host>:3000
http://<shared-host>:5678
http://<shared-host>:15672
<shared-host>:5672
```

Public server address, bind IP and host ports are configurable through `.env`.

`SHARED_PUBLIC_HOST` is the IP/DNS advertised to clients on other hosts.

Published shared services предназначены для trusted LAN/VPN и MUST быть
ограничены host firewall.

## AI ownership

Physical model/revision, GPU list, TP/DP и runtime limits принадлежат
`shared-infrastructure`, а не business project.

Business projects используют logical contracts:

```text
shared-vlm
shared-embedding
```

и не поднимают собственную копию общей модели без документированной причины.

## Проверка

```bash
docker compose ps
./scripts/check.sh
nvidia-smi
```

Runtime state всегда проверяется отдельно: этот registry описывает intended
topology, а не доказывает, что container сейчас запущен.
