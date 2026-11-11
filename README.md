# Motor Multi-Tenant Assíncrono para WhatsApp

Microsserviço assíncrono de alto desempenho desenvolvido em Python 3.12+, FastAPI, LangGraph, PostgreSQL (`pgvector` com índice HNSW), Redis e Alembic, projetado para automação conversacional em escala comercial, recuperação semântica de contexto vetorial (RAG), conciliação financeira de pagamentos e fechamento de pedidos via WhatsApp com integração à Evolution API v2.

O sistema opera com isolamento rigoroso multi-tenant, permitindo que dezenas de empresas de segmentos distintos (distribuidoras, lanchonetes e pizzarias, depósitos de construção, farmácias) compartilhem a infraestrutura com segregação lógica total em nível de banco de dados, filas, locks, sessões, cache, histórico e coleções vetoriais.

---

## 1. Visão Geral da Arquitetura e Engenharia de Software

O microsserviço foi concebido sob os princípios de Clean Architecture, Orientação a Eventos, Desacoplamento por Fila (Producer/Worker) e Modelagem de Estados Determinísticos via Grafos Direcionados Acíclicos (DAGs). A arquitetura desacopla as responsabilidades nas seguintes camadas operacionais:

```text
                                [ WhatsApp Cliente ]
                                         |
                                         v
                            [ Evolution API v2 Gateway ]
                                         |
                          (Webhook HTTP POST + SSL Caddy)
                                         |
                                         v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 1: Segurança de Borda, Ingestão e Desacoplamento Assíncrono              │
│ - Validação Criptográfica (X-Webhook-Secret via hmac.compare_digest)            │
│ - Tratamento Prioritário de Conectividade (CONNECTION_UPDATE)                   │
│ - Redis Event Deduplication (SET NX EX, TTL 300s por message_id)                │
│ - Producer Queue (<20ms): LPUSH em queue:webhooks:pending -> HTTP 200 queued    │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 2: Worker Dedicado, Locks Distribuídos e Tolerância a Falhas             │
│ - Consumo Atômico via BLMOVE (pending -> processing -> ACK ou DLQ)              │
│ - Redis Distributed Lock (chave atômica por tenant + usuário, lease 10s)        │
│ - Resolução e Isolamento Multi-Tenant (instance_name -> company_id UUID)        │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 3: Segurança Cognitiva (NVIDIA NeMo-Guardrails)                          │
│ - Input Rails (<20ms): Jailbreak, Prompt Injection, SQLi, Engenharia Social     │
│ - Definições Colang 1.0 (rails.co) e Políticas Declarativas (config.yml)        │
│ - Output Rails: Prevenção ativa contra vazamento de API Keys e URIs             │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 4: Motor de Estado Conversacional (LangGraph StateGraph)                 │
│ - Roteamento Adaptativo por Segmento (Pizzaria/Lanches, Delivery, Balcão)       │
│ - Transições de Estado: inicio -> coletando_itens -> endereco -> pagamento      │
│ - Detecção e Ativação de Transbordo Humano (human_handoff = True)               │
└───────────────────┬─────────────────────────────────────────┬───────────────────┘
                    |                                         |
                    v                                         v
┌──────────────────────────────────────┐  ┌───────────────────────────────────────┐
│ Camada 5: Memória & RAG pgvector     │  │ Camada 6: Visão Multimodal (Receipt)  │
│ - MessageHistory (Sliding Window N=6)│  │ - Captura de Mídia (imageMessage)     │
│ - knowledge_chunks VECTOR(768) HNSW  │  │ - Gemini 2.5 Flash Vision             │
│ - Embeddings gemini-embedding-001    │  │ - Auditoria de Comprovante Pix        │
│ - Distância Cosseno (<=>) no SQL     │  │ - Reconciliação Pagador vs Cliente    │
└───────────────────┬──────────────────┘  └───────────────────┬───────────────────┘
                    |                                         |
                    └────────────────────┬────────────────────┘
                                         |
                                         v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 7: Raciocínio Cognitivo e Despacho Resiliente (Evolution Client)         │
│ - LangChain + Gemini 2.5 Flash com fallbacks determinísticos                    │
│ - Envio WhatsApp com Retry + Exponential Backoff (1s, 2s, 4s)                   │
│ - Despacho Assíncrono de Comanda de Produção para Cozinha/Gestor                │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 8: Observabilidade, Monitoramento de Queda e Persistência                │
│ - Registro em audit_logs (JSON estruturado, latência ms, event_type)            │
│ - Monitoramento CONNECTION_UPDATE + Push Alerts (ALERT_WEBHOOK_URL + Cooldown)  │
│ - Migrações Versionadas Alembic (alembic upgrade head)                          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detalhamento Técnico das Camadas

### Camada 1: Segurança de Borda e Ingestão Assíncrona
- **Autenticação Criptográfica (`app/security.py`):** O endpoint `POST /webhook/evolution` valida o segredo compartilhado (`WEBHOOK_SECRET`) usando comparação em tempo constante (`hmac.compare_digest`) via header `X-Webhook-Secret`, `apikey` ou query param `token`. Endpoints administrativos exigem `ADMIN_API_KEY` via `X-Admin-Key` ou `Authorization: Bearer`.
- **Deduplicação de Eventos:** Cada webhook entrante possui um identificador único (`message_id`). O sistema executa atomicamente `SET NX EX` no Redis sob a chave `dedup:msg:{message_id}` com expiração de 300 segundos, descartando entregas duplicadas com status HTTP 200 `{ "status": "ignored", "reason": "duplicate_event" }`.
- **Desacoplamento Producer/Worker (`ASYNC_QUEUE_ENABLED=true`):** A API valida, deduplica na borda e publica o evento na fila `queue:webhooks:pending` via `LPUSH`, retornando `200 OK {"status": "queued"}` em menos de 20ms sem bloquear conexões HTTP durante chamadas a modelos de IA.

### Camada 2: Worker Dedicado, Locks e Isolamento Multi-Tenant
- **Consumo Confiável (`app/queue_worker.py`):** Containers `worker` dedicados consomem eventos atomicamente via `BLMOVE` (`queue:webhooks:pending` -> `queue:webhooks:processing`), aplicando ACK após conclusão ou re-enfileiramento até `MAX_JOB_ATTEMPTS = 3`. Falhas definitivas são encaminhadas para a Dead-Letter Queue (`queue:webhooks:dlq`).
- **Distributed Lock por Cliente:** Para evitar condições de corrida quando um cliente envia várias mensagens em rajada, o sistema adquire trava atômica no Redis sob `lock:company:{company_id}:user:{phone_number}` (lease de 10s, liberação validada por token UUID).
- **Segregação Multi-Tenant:** `company_id` (UUID v4), resolvido a partir de `instance_name`, atua como chave de partição lógica obrigatória em todas as consultas (`customer_sessions`, `order_records`, `message_history`, `knowledge_chunks`, `audit_logs`).

### Camada 3: Segurança de IA (NVIDIA NeMo-Guardrails)
- **Defesa em Profundidade (`app/guardrails_service.py`):** Combina definições em Colang 1.0 (`app/guardrails/rails.co`) e configurações declarativas (`app/guardrails/config.yml`).
- **Input Rails (<20ms):** Bloqueia tentativas de Jailbreak (*DAN mode*), Prompt Injection (*ignore all previous instructions*), injeções SQL (`DROP TABLE`, `UNION SELECT`) e Engenharia Social na borda antes de consumir tokens do LLM.
- **Output Rails:** Sanitiza respostas do modelo antes do envio, bloqueando qualquer vazamento de chaves de API (`AQ.*`, `AIza*`, `sk-*`) ou strings de conexão (`postgresql://`, `redis://`).
- **Telemetria de Ameaças:** Bloqueios geram eventos `SECURITY_INPUT_RAIL_BLOCKED` na trilha `audit_logs`.

### Camada 4: Máquina de Estados Conversacional (LangGraph)
- Orquestrada por `StateGraph` implementando `BotState`, com roteamento condicional por segmento:
  - *Pizzarias e Lanchonetes:* Atendimento fluido sem menus robóticos, com saudação estruturada em cinco categorias (Pizzas Tradicionais, Especiais, Doces, Smash Burgers e Bebidas Geladas).
  - *Distribuidoras de Bebidas/Gás:* Fluxo determinístico de coleta de itens, endereço, pagamento e comanda de expedição.
  - *Depósitos de Materiais e Farmácias:* Triagem com busca vetorial RAG no catálogo e transbordo humano prioritário (`human_handoff = True`).

### Camada 5: Memória Deslizante e RAG Vetorial em Escala (`pgvector` HNSW)
- **Memória Deslizante:** `get_chat_history` retorna os últimos 6 turnos ordenados cronologicamente com timestamps monotonicamente crescentes (`app/rag.py`).
- **Embeddings Semânticos (`app/embeddings.py`):** Gera vetores densos de 768 dimensões usando `models/gemini-embedding-001` com cache em Redis (TTL 24h) e fallback determinístico offline para testes.
- **Índice HNSW Nativo (`app/models.py` / `app/database.py`):** A coluna `knowledge_chunks.embedding` utiliza tipo `VECTOR(768)` no PostgreSQL indexado via `HNSW (vector_cosine_ops)` executando operador de distância de cosseno (`<=>`) diretamente no SQL, mantendo variante `JSON` para compatibilidade com SQLite nos testes offline.

### Camada 6: Raciocínio Cognitivo, Visão Multimodal e Despacho Resiliente
- **Gemini 2.5 Flash & Vision:** Extrai dados de comprovantes Pix enviados em imagem/documento (pagador bancário, valor, data/hora e ID de autenticação), conciliando pagamentos feitos por terceiros e enviando comanda segregada para o WhatsApp da cozinha (`whatsapp_cozinha`).
- **Evolution Client com Retry (`app/evolution_client.py`):** Envios via Evolution API aplicam backoff exponencial (`1s, 2s, 4s`) contra erros transitórios 5xx/429/rede, registrando `WHATSAPP_SEND_FAILED` na auditoria em falha definitiva.

### Camada 7: Observabilidade, Monitoramento de Queda e Migrações
- **Monitoramento Ativo `CONNECTION_UPDATE` (`app/connection_monitor.py`):** Detecta desconexões (`close`, `refused`, `disconnected`), grava alerta crítico (`WHATSAPP_DISCONNECTED_ALERT`), persiste `disconnected_at`, calcula duração da queda e dispara push notification para `ALERT_WEBHOOK_URL` com cooldown de 30 minutos no Redis.
- **Migrações Versionadas (`alembic/`):** Evolução controlada do schema PostgreSQL e criação automática de extensões e índices HNSW via `alembic upgrade head`.

### Camada 8: Suíte de Testes Automatizados (30 Testes Offline, Custo Zero)
- Executada via `pytest` com `sqlite+aiosqlite:///:memory:`, `fakeredis.aioredis.FakeRedis` e mocks determinísticos cobrindo todo o ciclo funcional, segurança NeMo-Guardrails, monitoramento de conexão e hardening V2.

---

## 3. Estrutura de Diretórios do Projeto

```text
.
├── pyproject.toml                       # Gerenciador de dependências uv e configurações pytest
├── uv.lock                              # Lockfile determinístico de dependências
├── alembic.ini                          # Configuração do Alembic para migrações assíncronas
├── alembic/
│   ├── env.py                           # Ambiente assíncrono lendo settings.async_postgres_url
│   └── versions/
│       └── 0001_initial_schema.py       # Migração baseline (tabelas + pgvector + HNSW)
├── Dockerfile                           # Imagem de produção com uvicorn --workers 2
├── docker-compose.yml                   # Stack local (Postgres, Redis, Evolution, API, Worker)
├── docker-compose.prod.yml              # Stack produção VPS (Caddy SSL + rede bridge isolada)
├── docker-compose.ip-test.yml           # Stack de homologação direta por IP da VPS
├── Caddyfile                            # Proxy reverso com HTTPS automático Let's Encrypt
├── .env.example                         # Variáveis básicas de desenvolvimento local
├── .env.prod.example                    # Template completo de variáveis de produção V2
├── app/
│   ├── config.py                        # Configurações tipadas via Pydantic Settings
│   ├── security.py                      # Autenticação de borda (WEBHOOK_SECRET e ADMIN_API_KEY)
│   ├── database.py                      # Engine async, EventDeduplicator, DistributedLock e busca SQL pgvector
│   ├── models.py                        # Modelos SQLAlchemy 2.0 + Vector(768) com HNSW
│   ├── schemas.py                       # Contratos Pydantic e parsers Evolution API v2
│   ├── embeddings.py                    # Geração semântica gemini-embedding-001 + cache Redis + fallback
│   ├── evolution_client.py              # Cliente HTTP Evolution API v2 com backoff exponencial
│   ├── connection_monitor.py            # Monitoramento CONNECTION_UPDATE e push alerts com cooldown
│   ├── guardrails/
│   │   ├── config.yml                   # Configuração declarativa NVIDIA NeMo-Guardrails
│   │   └── rails.co                     # Fluxos Colang 1.0 (Jailbreak, Prompt Injection, SQLi, Social Eng.)
│   ├── guardrails_service.py            # Motor de defesa em profundidade Input/Output Rails
│   ├── rag.py                           # Memória deslizante (monotonic UTC), catálogo e system prompt
│   ├── vision.py                        # Auditoria multimodal de comprovantes Pix (Gemini Vision)
│   ├── graph.py                         # Máquina de estados e nós cognitivos LangGraph
│   ├── webhook_processor.py             # Pipeline unificado de processamento de mensagens
│   ├── queue_worker.py                  # Producer Redis (LPUSH) e Worker dedicado (BLMOVE + ACK/DLQ)
│   ├── logging_config.py                # Logs estruturados em JSON ISO8601
│   ├── init_db.py                       # Script legado de inicialização direta
│   ├── seed_lanchonete.py               # Seed idempotente do primeiro tenant e catálogo RAG
│   └── main.py                          # Aplicação FastAPI, rotas de webhook, auditoria e saúde
├── scripts/
│   ├── deploy_vps.sh                    # Bootstrap e hardening Linux VPS (UFW, Fail2ban, Swap 4GB, Docker)
│   ├── start_prod.sh                    # Subida da stack prod + alembic upgrade head + seed
│   ├── start_test_ip.sh                 # Subida em modo teste por IP + alembic + seed
│   ├── backup_prod.sh                   # Backup diário PostgreSQL + sessões Evolution (/evolution/instances)
│   ├── setup_evolution.py               # Provisionamento automático de instância, webhook e QR Code
│   └── generate_pdf_report.py           # Gerador do relatório PDF de testes e evidências
└── tests/
    ├── conftest.py                      # Configuração offline determinística da suíte pytest
    ├── test_harness.py                  # 13 testes de fluxo multi-tenant, RAG, memória e visão Pix
    ├── test_guardrails_security.py      # 7 testes de segurança Red Teaming NVIDIA NeMo-Guardrails
    ├── test_connection_monitoring.py    # 4 testes de monitoramento CONNECTION_UPDATE e volumes
    └── test_production_hardening.py     # 6 testes de hardening V2 (Auth, Fila/DLQ, Retry, Push, HNSW)
```

---

## 4. Variáveis de Ambiente

Para ambiente local utilize `.env` baseado em `.env.example`, e para produção utilize `.env.prod.example`:

| Variável | Descrição | Exemplo |
| :--- | :--- | :--- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciais do banco PostgreSQL 16 + pgvector | `whatsapp_engine_prod` |
| `REDIS_URL` | URI interna do Redis (deduplicação, locks, filas, cache) | `redis://redis:6379/0` |
| `EVOLUTION_API_URL` / `EVOLUTION_API_KEY` | URL e chave global da Evolution API v2 | `http://evolution-api:8080` |
| `GEMINI_API_KEY` | Chave Google AI Studio (Gemini 2.5 Flash e Embeddings) | `sua_chave_gemini` |
| `WEBHOOK_SECRET` | Segredo validado no header `X-Webhook-Secret` do webhook | `token_seguro_32_chars` |
| `ADMIN_API_KEY` | Chave Bearer / `X-Admin-Key` das rotas administrativas | `admin_key_32_chars` |
| `ASYNC_QUEUE_ENABLED` | Ativa ingestão via fila Redis + Worker dedicado | `true` |
| `ALERT_WEBHOOK_URL` | Webhook externo (Slack/Discord/n8n) para alertas de queda | `https://hooks.exemplo/alert` |
| `DOMAIN_API` / `DOMAIN_EVOLUTION` | Subdomínios roteados pelo Caddy com SSL automático | `api.seudominio.com` |

---

## 5. Instruções de Execução e Implantação

### 5.1. Ambiente Local (Desenvolvimento)

Suba a stack completa (PostgreSQL pgvector, Redis, Evolution API v2, API FastAPI e Worker):

```bash
docker compose up -d --build
docker compose ps
```

Aplique as migrações Alembic e execute o seed inicial do tenant:

```bash
docker compose exec api alembic upgrade head
docker compose exec api python -m app.seed_lanchonete
```

### 5.2. Implantação em Produção (VPS Linux Ubuntu 22.04 / 24.04)

Na VPS limpa como `root`:

```bash
bash scripts/deploy_vps.sh
cd /opt/whatsapp-engine
cp .env.prod.example .env
nano .env
bash scripts/start_prod.sh
```

Para homologação rápida via IP sem domínio/SSL:

```bash
bash scripts/start_test_ip.sh
```

### 5.3. Pareamento do WhatsApp (QR Code)

Para criar a instância, habilitar os eventos `MESSAGES_UPSERT` e `CONNECTION_UPDATE` (incluindo `X-Webhook-Secret` se configurado) e gerar o QR Code:

```bash
uv run python scripts/setup_evolution.py
```

---

## 6. Execução da Suíte de Testes

Para executar todos os 30 testes automatizados (isolamento multi-tenant, concorrência Redis, segurança NeMo-Guardrails, monitoramento de queda, fila assíncrona/DLQ, backoff exponencial e autenticação):

```bash
uv run pytest -v
```

Suítes incluídas:
- `tests/test_harness.py` (13 testes): Isolamento de tenants, ciclo completo de pedidos, RAG, memória deslizante, visão de comprovantes Pix e transbordo humano.
- `tests/test_guardrails_security.py` (7 testes): Proteção ativa NVIDIA NeMo-Guardrails contra Jailbreak DAN, Prompt Injection, extração de chaves e falsos positivos.
- `tests/test_connection_monitoring.py` (4 testes): Alertas `CONNECTION_UPDATE`, restauração, endpoints de saúde e resiliência Docker.
- `tests/test_production_hardening.py` (6 testes): Autenticação de borda (`WEBHOOK_SECRET`, `ADMIN_API_KEY`), fila assíncrona Redis + Worker + DLQ, backoff exponencial, cooldown de push alerts e compilação nativa `pgvector` HNSW.

---

## 7. Especificação dos Endpoints

| Método | Rota | Autenticação | Descrição |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Pública | Verificação de disponibilidade e integridade do serviço. |
| `POST` | `/webhook/evolution` | `X-Webhook-Secret` | Receptor de eventos Evolution API v2 (mensagens e `CONNECTION_UPDATE`). |
| `GET` | `/api/instances/health` | `ADMIN_API_KEY` | Monitoramento global de saúde e disponibilidade das instâncias WhatsApp. |
| `GET` | `/api/companies/{id}/connection-status` | `ADMIN_API_KEY` | Status em tempo real, duração de queda e alertas de reconexão por empresa. |
| `GET` | `/audit/logs/{company_id}` | `ADMIN_API_KEY` | Consulta da trilha de auditoria e telemetria filtrada estritamente por tenant. |
| `GET` | `/docs` | Pública | Documentação interativa OpenAPI / Swagger UI. |

---

## 8. Boas Práticas e Alta Disponibilidade da Evolution API (99%+ Uptime)

Para garantir resiliência operacional e eliminar perda de atendimento por reinicializações ou falhas físicas no aparelho do lojista, o sistema adota três diretrizes mandatórias de produção:

1. **Reinicialização Automática (`restart: always`):** Todos os serviços (`evolution`, `postgres`, `redis`, `api`, `worker`, `caddy`) nos arquivos compose utilizam `restart: always`, garantindo recuperação automática em segundos após qualquer oscilação de memória ou reboot da VPS.
2. **Persistência Estrita de Sessão e Backups:** Os diretórios `/evolution/instances` e `/evolution/store` são persistidos em volumes nomeados do Docker, evitando releitura de QR Code após updates de container. O script `scripts/backup_prod.sh` realiza backup diário automatizado do PostgreSQL e dos arquivos de sessão com retenção de 7 dias.
3. **Monitoramento Ativo de Queda (`CONNECTION_UPDATE`):** Eventos de desconexão (`close`, `refused`, `disconnected`) acionam o `ConnectionMonitorService`, gravando alerta crítico na auditoria, calculando tempo exato de inatividade e disparando push notification com cooldown de 30 minutos.

---

## 9. Licença e Autoria

Projeto concebido para operações comerciais multi-tenant de alta demanda, arquitetado sob rigorosos padrões de engenharia de software para microsserviços corporativos assíncronos.
