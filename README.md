# Motor Multi-Tenant Assincrono para WhatsApp

Microsservico assincrono de alto desempenho desenvolvido em Python 3.12+, FastAPI, LangGraph, PostgreSQL (pgvector com indice HNSW), Redis e Alembic, projetado para automacao conversacional em escala comercial, recuperacao semantica de contexto vetorial (RAG), conciliacao financeira de pagamentos e fechamento de pedidos via WhatsApp com integracao a Evolution API v2.

O sistema opera com isolamento rigoroso multi-tenant, permitindo que dezenas de empresas de segmentos distintos (distribuidoras, lanchonetes e pizzarias, depositos de construcao, farmacias) compartilhem a infraestrutura com segregacao logica total em nivel de banco de dados, filas, locks, sessoes, cache, historico e colecoes vetoriais.

---

## 1. Visao Geral da Arquitetura e Engenharia de Software

O microsservico foi concebido sob os principios de Clean Architecture, Orientacao a Eventos, Desacoplamento por Fila (Producer/Worker) e Modelagem de Estados Deterministicos via Grafos Direcionados Aciclicos (DAGs). A arquitetura desacopla as responsabilidades nas seguintes camadas operacionais:

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
│ Camada 1: Seguranca de Borda, Ingestao e Desacoplamento Assincrono              │
│ - Validacao Criptografica (X-Webhook-Secret via hmac.compare_digest)            │
│ - Tratamento Prioritario de Conectividade (CONNECTION_UPDATE)                   │
│ - Redis Event Deduplication (SET NX EX, TTL 300s por message_id)                │
│ - Producer Queue (<20ms): LPUSH em queue:webhooks:pending -> HTTP 200 queued    │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 2: Worker Dedicado, Locks Distribuidos e Tolerancia a Falhas             │
│ - Consumo Atomico via BLMOVE (pending -> processing -> ACK ou DLQ)              │
│ - Redis Distributed Lock (chave atomica por tenant + usuario, lease 10s)        │
│ - Resolucao e Isolamento Multi-Tenant (instance_name -> company_id UUID)        │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 3: Seguranca Cognitiva (NVIDIA NeMo-Guardrails)                          │
│ - Input Rails (<20ms): Jailbreak, Prompt Injection, SQLi, Engenharia Social     │
│ - Definicoes Colang 1.0 (rails.co) e Politicas Declarativas (config.yml)        │
│ - Output Rails: Prevencao ativa contra vazamento de API Keys e URIs             │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 4: Motor de Estado Conversacional (LangGraph StateGraph)                 │
│ - Roteamento Adaptativo por Segmento (Pizzaria/Lanches, Delivery, Balcao)       │
│ - Transicoes de Estado: inicio -> coletando_itens -> endereco -> pagamento      │
│ - Deteccao e Ativacao de Transbordo Humano (human_handoff = True)               │
└───────────────────┬─────────────────────────────────────────┬───────────────────┘
                    |                                         |
                    v                                         v
┌──────────────────────────────────────┐  ┌───────────────────────────────────────┐
│ Camada 5: Memoria & RAG pgvector     │  │ Camada 6: Visao Multimodal (Receipt)  │
│ - MessageHistory (Sliding Window N=6)│  │ - Captura de Midia (imageMessage)     │
│ - knowledge_chunks VECTOR(768) HNSW  │  │ - Gemini 2.5 Flash Vision             │
│ - Embeddings gemini-embedding-001    │  │ - Auditoria de Comprovante Pix        │
│ - Distancia Cosseno (<=>) no SQL     │  │ - Reconciliacao Pagador vs Cliente    │
└───────────────────┬──────────────────┘  └───────────────────┬───────────────────┘
                    |                                         |
                    └────────────────────┬────────────────────┘
                                         |
                                         v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 7: Raciocinio Cognitivo e Despacho Resiliente (Evolution Client)         │
│ - LangChain + Gemini 2.5 Flash com fallbacks deterministicos                    │
│ - Envio WhatsApp com Retry + Exponential Backoff (1s, 2s, 4s)                   │
│ - Despacho Assincrono de Comanda de Producao para Cozinha/Gestor                │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       |
                                       v
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 8: Observabilidade, Monitoramento de Queda e Persistencia                │
│ - Registro em audit_logs (JSON estruturado, latency ms, event_type)             │
│ - Monitoramento CONNECTION_UPDATE + Push Alerts (ALERT_WEBHOOK_URL + Cooldown)  │
│ - Migracoes Versionadas Alembic (alembic upgrade head)                          │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detalhamento Tecnico das Camadas

### Camada 1: Seguranca de Borda e Ingestao Assincrona
- **Autenticacao Criptografica (`app/security.py`):** O endpoint `POST /webhook/evolution` valida o segredo compartilhado (`WEBHOOK_SECRET`) usando comparacao em tempo constante (`hmac.compare_digest`) via header `X-Webhook-Secret`, `apikey` ou query param `token`. Endpoints administrativos exigem `ADMIN_API_KEY` via `X-Admin-Key` ou `Authorization: Bearer`.
- **Deduplicacao de Eventos:** Cada webhook entrante possui um identificador unico (`message_id`). O sistema executa atomicamente `SET NX EX` no Redis sob a chave `dedup:msg:{message_id}` com expiracao de 300 segundos, descartando entregas duplicadas com status HTTP 200 `{ "status": "ignored", "reason": "duplicate_event" }`.
- **Desacoplamento Producer/Worker (`ASYNC_QUEUE_ENABLED=true`):** A API valida, deduplica na borda e publica o evento na fila `queue:webhooks:pending` via `LPUSH`, retornando `200 OK {"status": "queued"}` em menos de 20ms sem bloquear conexoes HTTP durante chamadas a modelos de IA.

### Camada 2: Worker Dedicado, Locks e Isolamento Multi-Tenant
- **Consumo Confiavel (`app/queue_worker.py`):** Containers `worker` dedicados consomem eventos atomicamente via `BLMOVE` (`queue:webhooks:pending` -> `queue:webhooks:processing`), aplicando ACK apos conclusao ou re-enfileiramento ate `MAX_JOB_ATTEMPTS = 3`. Falhas definitivas sao encaminhadas para Dead-Letter Queue (`queue:webhooks:dlq`).
- **Distributed Lock por Cliente:** Para evitar condicoes de corrida quando um cliente envia varias mensagens em rajada, o sistema adquire trava atomica no Redis sob `lock:company:{company_id}:user:{phone_number}` (lease de 10s, liberacao validada por token UUID).
- **Segregacao Multi-Tenant:** `company_id` (UUID v4), resolvido a partir de `instance_name`, atua como chave de particao logica obrigatoria em todas as consultas (`customer_sessions`, `order_records`, `message_history`, `knowledge_chunks`, `audit_logs`).

### Camada 3: Seguranca de IA (NVIDIA NeMo-Guardrails)
- **Defesa em Profundidade (`app/guardrails_service.py`):** Combina definicoes em Colang 1.0 (`app/guardrails/rails.co`) e configuracoes declarativas (`app/guardrails/config.yml`).
- **Input Rails (<20ms):** Bloqueia tentativas de Jailbreak (*DAN mode*), Prompt Injection (*ignore all previous instructions*), injecoes SQL (`DROP TABLE`, `UNION SELECT`) e Engenharia Social na borda antes de consumir tokens do LLM.
- **Output Rails:** Sanitiza respostas do modelo antes do envio, bloqueando qualquer vazamento de chaves de API (`AQ.*`, `AIza*`, `sk-*`) ou strings de conexao (`postgresql://`, `redis://`).
- **Telemetria de Ameacas:** Bloqueios geram eventos `SECURITY_INPUT_RAIL_BLOCKED` na trilha `audit_logs`.

### Camada 4: Maquina de Estados Conversacional (LangGraph)
- Orquestrada por `StateGraph` implementando `BotState`, com roteamento condicional por segmento:
  - *Pizzarias e Lanchonetes:* Atendimento fluido sem menus roboticos, com saudacao estruturada em cinco categorias (Pizzas Tradicionais, Especiais, Doces, Smash Burgers e Bebidas Geladas).
  - *Distribuidoras de Bebidas/Gas:* Fluxo deterministico de coleta de itens, endereco, pagamento e comanda de expedicao.
  - *Depositos de Materiais e Farmacias:* Triagem com busca vetorial RAG no catalogo e transbordo humano prioritario (`human_handoff = True`).

### Camada 5: Memoria Deslizante e RAG Vetorial em Escala (`pgvector` HNSW)
- **Memoria Deslizante:** `get_chat_history` retorna os ultimos 6 turnos ordenados cronologicamente com timestamps monotonicamente crescentes (`app/rag.py`).
- **Embeddings Semânticos (`app/embeddings.py`):** Gera vetores densos de 768 dimensoes usando `models/gemini-embedding-001` com cache em Redis (TTL 24h) e fallback deterministico offline para testes.
- **Indice HNSW Nativo (`app/models.py` / `app/database.py`):** A coluna `knowledge_chunks.embedding` utiliza tipo `VECTOR(768)` no PostgreSQL indexado via `HNSW (vector_cosine_ops)` executando operador de distancia de cosseno (`<=>`) diretamente no SQL, mantendo variante `JSON` para compatibilidade com SQLite nos testes offline.

### Camada 6: Raciocinio Cognitivo, Visao Multimodal e Despacho Resiliente
- **Gemini 2.5 Flash & Vision:** Extrai dados de comprovantes Pix enviados em imagem/documento (pagador bancario, valor, data/hora e ID de autenticacao), conciliando pagamentos feitos por terceiros e enviando comanda segregada para o WhatsApp da cozinha (`whatsapp_cozinha`).
- **Evolution Client com Retry (`app/evolution_client.py`):** Envios via Evolution API aplicam backoff exponencial (`1s, 2s, 4s`) contra erros transitórios 5xx/429/rede, registrando `WHATSAPP_SEND_FAILED` na auditoria em falha definitiva.

### Camada 7: Observabilidade, Monitoramento de Queda e Migracoes
- **Monitoramento Ativo `CONNECTION_UPDATE` (`app/connection_monitor.py`):** Detecta desconexoes (`close`, `refused`, `disconnected`), grava alerta critico (`WHATSAPP_DISCONNECTED_ALERT`), persiste `disconnected_at`, calcula duracao da queda e dispara push notification para `ALERT_WEBHOOK_URL` com cooldown de 30 minutos no Redis.
- **Migracoes Versionadas (`alembic/`):** Evolucao controlada do schema PostgreSQL e criacao automatica de extensoes e indices HNSW via `alembic upgrade head`.

### Camada 8: Suite de Testes Automatizados (30 Testes Offline, Custo Zero)
- Executada via `pytest` com `sqlite+aiosqlite:///:memory:`, `fakeredis.aioredis.FakeRedis` e mocks deterministicos cobrindo todo o ciclo funcional, seguranca NeMo-Guardrails, monitoramento de conexao e hardening V2.

---

## 3. Estrutura de Diretorios do Projeto

```text
.
├── pyproject.toml                       # Gerenciador de dependencias uv e configuracoes pytest
├── uv.lock                              # Lockfile deterministico de dependencias
├── alembic.ini                          # Configuracao do Alembic para migracoes assincronas
├── alembic/
│   ├── env.py                           # Ambiente assincrono lendo settings.async_postgres_url
│   └── versions/
│       └── 0001_initial_schema.py       # Migracao baseline (tabelas + pgvector + HNSW)
├── Dockerfile                           # Imagem de producao com uvicorn --workers 2
├── docker-compose.yml                   # Stack local (Postgres, Redis, Evolution, API, Worker)
├── docker-compose.prod.yml              # Stack producao VPS (Caddy SSL + rede bridge isolada)
├── docker-compose.ip-test.yml           # Stack de homologacao direta por IP da VPS
├── Caddyfile                            # Proxy reverso com HTTPS automatico Let's Encrypt
├── .env.example                         # Variaveis basicas de desenvolvimento local
├── .env.prod.example                    # Template completo de variaveis de producao V2
├── app/
│   ├── config.py                        # Configuracoes tipadas via Pydantic Settings
│   ├── security.py                      # Autenticacao de borda (WEBHOOK_SECRET e ADMIN_API_KEY)
│   ├── database.py                      # Engine async, EventDeduplicator, DistributedLock e busca SQL pgvector
│   ├── models.py                        # Modelos SQLAlchemy 2.0 + Vector(768) com HNSW
│   ├── schemas.py                       # Contratos Pydantic e parsers Evolution API v2
│   ├── embeddings.py                    # Geracao semantica gemini-embedding-001 + cache Redis + fallback
│   ├── evolution_client.py              # Cliente HTTP Evolution API v2 com backoff exponencial
│   ├── connection_monitor.py            # Monitoramento CONNECTION_UPDATE e push alerts com cooldown
│   ├── guardrails/
│   │   ├── config.yml                   # Configuracao declarativa NVIDIA NeMo-Guardrails
│   │   └── rails.co                     # Fluxos Colang 1.0 (Jailbreak, Prompt Injection, SQLi, Social Eng.)
│   ├── guardrails_service.py            # Motor de defesa em profundidade Input/Output Rails
│   ├── rag.py                           # Memoria deslizante (monotonic UTC), catalogo e system prompt
│   ├── vision.py                        # Auditoria multimodal de comprovantes Pix (Gemini Vision)
│   ├── graph.py                         # Maquina de estados e nos cognitivos LangGraph
│   ├── webhook_processor.py             # Pipeline unificado de processamento de mensagens
│   ├── queue_worker.py                  # Producer Redis (LPUSH) e Worker dedicado (BLMOVE + ACK/DLQ)
│   ├── logging_config.py                # Logs estruturados em JSON ISO8601
│   ├── init_db.py                       # Script legado de inicializacao direta
│   ├── seed_lanchonete.py               # Seed idempotente do primeiro tenant e catalogo RAG
│   └── main.py                          # Aplicacao FastAPI, rotas de webhook, auditoria e saude
├── scripts/
│   ├── deploy_vps.sh                    # Bootstrap e hardening Linux VPS (UFW, Fail2ban, Swap 4GB, Docker)
│   ├── start_prod.sh                    # Subida da stack prod + alembic upgrade head + seed
│   ├── start_test_ip.sh                 # Subida em modo teste por IP + alembic + seed
│   ├── backup_prod.sh                   # Backup diario PostgreSQL + sessoes Evolution (/evolution/instances)
│   ├── setup_evolution.py               # Provisionamento automatico de instancia, webhook e QR Code
│   └── generate_pdf_report.py           # Gerador do relatorio PDF de testes e evidencias
└── tests/
    ├── conftest.py                      # Configuracao offline deterministica da suite pytest
    ├── test_harness.py                  # 13 testes de fluxo multi-tenant, RAG, memoria e visao Pix
    ├── test_guardrails_security.py      # 7 testes de seguranca Red Teaming NVIDIA NeMo-Guardrails
    ├── test_connection_monitoring.py    # 4 testes de monitoramento CONNECTION_UPDATE e volumes
    └── test_production_hardening.py     # 6 testes de hardening V2 (Auth, Fila/DLQ, Retry, Push, HNSW)
```

---

## 4. Variaveis de Ambiente

Para ambiente local utilize `.env` baseado em `.env.example`, e para producao utilize `.env.prod.example`:

| Variavel | Descricao | Exemplo |
| :--- | :--- | :--- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Credenciais do banco PostgreSQL 16 + pgvector | `whatsapp_engine_prod` |
| `REDIS_URL` | URI interna do Redis (deduplicacao, locks, filas, cache) | `redis://redis:6379/0` |
| `EVOLUTION_API_URL` / `EVOLUTION_API_KEY` | URL e chave global da Evolution API v2 | `http://evolution-api:8080` |
| `GEMINI_API_KEY` | Chave Google AI Studio (Gemini 2.5 Flash e Embeddings) | `sua_chave_gemini` |
| `WEBHOOK_SECRET` | Segredo validado no header `X-Webhook-Secret` do webhook | `token_seguro_32_chars` |
| `ADMIN_API_KEY` | Chave Bearer / `X-Admin-Key` das rotas administrativas | `admin_key_32_chars` |
| `ASYNC_QUEUE_ENABLED` | Ativa ingestao via fila Redis + Worker dedicado | `true` |
| `ALERT_WEBHOOK_URL` | Webhook externo (Slack/Discord/n8n) para alertas de queda | `https://hooks.exemplo/alert` |
| `DOMAIN_API` / `DOMAIN_EVOLUTION` | Subdominios roteados pelo Caddy com SSL automatico | `api.seudominio.com` |

---

## 5. Instrucoes de Execucao e Implantacao

### 5.1. Ambiente Local (Desenvolvimento)

Suba a stack completa (PostgreSQL pgvector, Redis, Evolution API v2, API FastAPI e Worker):

```bash
docker compose up -d --build
docker compose ps
```

Aplique as migracoes Alembic e execute o seed inicial do tenant:

```bash
docker compose exec api alembic upgrade head
docker compose exec api python -m app.seed_lanchonete
```

### 5.2. Implantacao em Producao (VPS Linux Ubuntu 22.04 / 24.04)

Na VPS limpa como `root`:

```bash
bash scripts/deploy_vps.sh
cd /opt/whatsapp-engine
cp .env.prod.example .env
nano .env
bash scripts/start_prod.sh
```

Para homologacao rapida via IP sem dominio/SSL:

```bash
bash scripts/start_test_ip.sh
```

### 5.3. Pareamento do WhatsApp (QR Code)

Para criar a instancia, habilitar os eventos `MESSAGES_UPSERT` e `CONNECTION_UPDATE` (incluindo `X-Webhook-Secret` se configurado) e gerar o QR Code:

```bash
uv run python scripts/setup_evolution.py
```

---

## 6. Execucao da Suite de Testes

Para executar todos os 30 testes automatizados (isolamento multi-tenant, concorrencia Redis, seguranca NeMo-Guardrails, monitoramento de queda, fila assincrona/DLQ, backoff exponencial e autenticacao):

```bash
uv run pytest -v
```

Suites incluidas:
- `tests/test_harness.py` (13 testes): Isolamento de tenants, ciclo completo de pedidos, RAG, memoria deslizante, visao de comprovantes Pix e transbordo humano.
- `tests/test_guardrails_security.py` (7 testes): Protecao ativa NVIDIA NeMo-Guardrails contra Jailbreak DAN, Prompt Injection, extracao de chaves e falsos positivos.
- `tests/test_connection_monitoring.py` (4 testes): Alertas `CONNECTION_UPDATE`, restauracao, endpoints de saude e resiliencia Docker.
- `tests/test_production_hardening.py` (6 testes): Autenticacao de borda (`WEBHOOK_SECRET`, `ADMIN_API_KEY`), fila assincrona Redis + Worker + DLQ, backoff exponencial, cooldown de push alerts e compilacao nativa `pgvector` HNSW.

---

## 7. Especificacao dos Endpoints

| Metodo | Rota | Autenticacao | Descricao |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Publica | Verificacao de disponibilidade e integridade do servico. |
| `POST` | `/webhook/evolution` | `X-Webhook-Secret` | Receptor de eventos Evolution API v2 (mensagens e `CONNECTION_UPDATE`). |
| `GET` | `/api/instances/health` | `ADMIN_API_KEY` | Monitoramento global de saude e disponibilidade das instancias WhatsApp. |
| `GET` | `/api/companies/{id}/connection-status` | `ADMIN_API_KEY` | Status em tempo real, duracao de queda e alertas de reconexao por empresa. |
| `GET` | `/audit/logs/{company_id}` | `ADMIN_API_KEY` | Consulta da trilha de auditoria e telemetria filtrada estritamente por tenant. |
| `GET` | `/docs` | Publica | Documentacao interativa OpenAPI / Swagger UI. |

---

## 8. Boas Praticas e Alta Disponibilidade da Evolution API (99%+ Uptime)

Para garantir resiliencia operacional e eliminar perda de atendimento por reinicializacoes ou falhas fisicas no aparelho do lojista, o sistema adota tres diretrizes mandatorias de producao:

1. **Reinicializacao Automatica (`restart: always`):** Todos os servicos (`evolution`, `postgres`, `redis`, `api`, `worker`, `caddy`) nos arquivos compose utilizam `restart: always`, garantindo recuperacao automatica em segundos apos qualquer oscilacao de memoria ou reboot da VPS.
2. **Persistencia Estrita de Sessao e Backups:** Os diretorios `/evolution/instances` e `/evolution/store` sao persistidos em volumes nomeados do Docker, evitando releitura de QR Code apos updates de container. O script `scripts/backup_prod.sh` realiza backup diario automatizado do PostgreSQL e dos arquivos de sessao com retencao de 7 dias.
3. **Monitoramento Ativo de Queda (`CONNECTION_UPDATE`):** Eventos de desconexao (`close`, `refused`, `disconnected`) acionam o `ConnectionMonitorService`, gravando alerta critico na auditoria, calculando tempo exato de inatividade e disparando push notification com cooldown de 30 minutos.

---

## 9. Licenca e Autoria

Projeto concebido para operacoes comerciais multi-tenant de alta demanda, arquitetado sob rigorosos padroes de engenharia de software para microsservicos corporativos assincronos.
