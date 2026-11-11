# Motor Multi-Tenant Assincrono para WhatsApp

Microsservico assincrono de alto desempenho desenvolvido em Python 3.12+, FastAPI, LangGraph, PostgreSQL (pgvector) e Redis, projetado para automacao conversacional, recuperacao de contexto vetorial (RAG), conciliacao financeira de pagamentos e fechamento de pedidos via WhatsApp com integracao a Evolution API v2.

O sistema opera com isolamento rigoroso multi-tenant, permitindo que multiplas empresas de segmentos distintos (distribuidoras, lanchonetes e pizzarias, depositos de construcao, farmacias) compartilhem a infraestrutura com segregacao logica total em nivel de banco de dados, sessoes, cache, historico e colecoes vetoriais.

---

## 1. Visao Geral da Arquitetura e Engenharia de Software

O microsservico foi concebido sob os principios de Clean Architecture, Orientacao a Eventos e Modelagem de Estados Deterministicos via Grafos Direcionados Aciclicos (DAGs). A arquitetura desacopla as responsabilidades em oito camadas operacionais:

```text
                                [ WhatsApp Cliente ]
                                         │
                                         ▼
                            [ Evolution API v2 Gateway ]
                                         │
                               (Webhook HTTP POST)
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 1: Ingestao, Deduplicacao e Controle de Concorrencia                     │
│ - FastAPI Endpoint (/webhook/evolution)                                         │
│ - Redis Event Deduplication (SET NX EX, TTL 300s por message_id)                │
│ - Redis Distributed Lock (chave atomica por tenant + usuario, lease 10s)        │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 2: Resolucao e Isolamento Multi-Tenant                                   │
│ - Mapeamento instance_name -> company_id (UUID) -> owner_id                     │
│ - Segregacao relacional estrita (SQLAlchemy 2.0 Async Session)                  │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 3: Motor de Estado Conversacional (LangGraph StateGraph)                  │
│ - Roteamento Adaptativo por Segmento (Pizzaria/Lanches, Delivery, Balcao)       │
│ - Transicoes de Estado: inicio -> coletando_itens -> endereco -> pagamento     │
│ - Deteccao e Ativacao de Transbordo Humano (human_handoff = True)               │
└───────────────────┬─────────────────────────────────────────┬───────────────────┘
                    │                                         │
                    ▼                                         ▼
┌──────────────────────────────────────┐  ┌───────────────────────────────────────┐
│ Camada 4: Memoria Deslizante e RAG   │  │ Camada 5: Visao Multimodal (Receipt)  │
│ - MessageHistory (Sliding Window N=6)│  │ - Identificacao de Midia (imageMessage│
│ - knowledge_chunks (pgvector 768 dim)│  │ - Gemini 2.5 Flash Vision             │
│ - Similaridade Cosseno (<=>)         │  │ - Auditoria de Comprovante Pix        │
│ - Injecao Dinamica de System Prompt  │  │ - Deteccao de Pagador vs Cliente      │
└───────────────────┬──────────────────┘  └───────────────────┬───────────────────┘
                    │                                         │
                    └────────────────────┬────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 6: Raciocinio Cognitivo (LangChain + Gemini 2.5 Flash)                   │
│ - Inferencias contextuais com fallbacks deterministicos                         │
│ - Sanitizacao de respostas e remocao de marcadores rigidos                      │
│ - Despacho Assincrono de Notificacao para Cozinha/Gestor via WhatsApp           │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Camada 7: Observabilidade, Auditoria Estruturada e Persistencia                 │
│ - Registro em audit_logs (JSON estruturado, latency ms, event_type)            │
│ - Emissao de logs padrao ISO8601 em stdout e arquivos rotativos                 │
│ - Endpoint de Auditoria (/audit/logs/{company_id})                              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detalhamento Tecnico das Camadas

### Camada 1: Ingestao e Controle de Concorrencia Distribuida
- **Deduplicacao de Eventos:** Cada webhook entrante possui um identificador unico (`message_id`). O sistema executa atomicamente `SET NX EX` no Redis sob a chave `dedup:msg:{message_id}` com expiracao de 300 segundos. Mensagens reenviadas ou duplicadas pelo gateway da Evolution API sao descartadas com status HTTP 200 `{ "status": "ignored", "reason": "duplicate_event" }`, impedindo execucoes redundantes.
- **Distributed Lock por Cliente:** Para evitar condicoes de corrida quando o cliente envia multiplas mensagens sequenciais em milissegundos, o sistema adquire uma trava distribuida no Redis sob o padrao `lock:company:{company_id}:user:{phone_number}` com lease timeout de 10 segundos. Em caso de colisao concorrente, responde imediatamente HTTP 429 `{ "status": "busy", "reason": "lock_contention" }` e cancela a deduplicacao previa para permitir retentativas do gateway.

### Camada 2: Isolamento e Segregacao Multi-Tenant
- A identificacao do tenant e resolvida a partir de `instance_name` (chave unica indexada na tabela `companies`).
- O identificador `company_id` (UUID v4) atua como chave de particao logica em todas as consultas relacionais (`customer_sessions`, `order_records`, `message_history`, `audit_logs`).
- A propriedade `owner_id` permite segregacao administrativa, vinculando grupos de empresas a socios ou gestores especificos sem vazamento cruzado.

### Camada 3: Motor Vetorial e RAG (Retrieval-Augmented Generation)
- **Extensao pgvector:** A tabela `knowledge_chunks` persiste fragmentos textuais indexados por vetor denso de 768 dimensoes.
- **Filtro Estrito por Tenant:** As consultas de similaridade utilizam a distancia de cosseno (`<=>`) filtradas impreterivelmente por `WHERE company_id = :company_id`. Dados de catalogo, precos ou regras de entrega de uma empresa jamais sao acessados por consultas de outros tenants.

### Camada 4: Maquina de Estados Conversacional (LangGraph)
- O fluxo de atendimento e orquestrado por um grafo de estados (`StateGraph`) implementando a interface `BotState`.
- **Roteamento Condicional por Segmento:**
  - *Pizzarias e Lanchonetes:* Direcionamento imediato para raciocinio cognitivo com catalogo e saudacao estruturada em cinco categorias com faixas de preco (Pizzas Tradicionais, Especiais, Doces, Smash Burgers e Bebidas Geladas), sem obrigar navegacao robotica.
  - *Distribuidoras de Bebidas/Gas:* Maquina de estados deterministica com estagios de coleta de itens, endereco, confirmacao e emissao de comanda de expedicao.
  - *Depositos de Materiais e Farmacias:* Triagem de orcamento via busca vetorial no catalogo e transbordo humano prioritario (`human_handoff = True`).

### Camada 5: Memoria Conversacional Deslizante (Sliding Window)
- Toda mensagem recebida de clientes (`role = "user"`) e respostas emitidas pelo motor (`role = "assistant"`) sao persistidas na tabela `message_history`.
- O recuperador `get_chat_history` implementa uma janela deslizante retornando as ultimas N mensagens (padrao: 6 turnos) ordenadas cronologicamente, convertendo-as para `HumanMessage` e `AIMessage` do ecossistema LangChain.

### Camada 6: Raciocinio Cognitivo e Visao Multimodal
- **Inferencia com Gemini 2.5 Flash:** O nó cognitivo instancia `ChatGoogleGenerativeAI(model="gemini-2.5-flash", api_version="v1beta")`, permitindo API Keys proprias por empresa armazenadas em `Company.settings["gemini_api_key"]`.
- **Auditoria Multimodal de Comprovantes Pix (Gemini Vision):**
  - Quando o cliente envia imagem ou documento no WhatsApp, o sistema captura a midia via Evolution API e executa extracao visual estruturada.
  - O modelo identifica o nome do pagador bancario, valor, data/hora e codigo de transacao.
  - O sistema concilia o titular bancario mesmo quando for distinto do cliente solicitante (exemplo: pedido feito por *Eduardo Souza*, porem pago via conta de *Maria Souza*).
  - A confirmacao do pagamento e enviada ao cliente e a comanda da cozinha e despachada segregando o nome do cliente e o nome do titular do Pix no extrato.

### Camada 7: Seguranca de Nivel Corporativo (NVIDIA NeMo-Guardrails)
- **Defesa em Profundidade (Defense in Depth):** Implementacao de rails de protecao baseados no framework oficial NVIDIA NeMo-Guardrails, combinando definicoes em Colang (`rails.co`) e politicas de seguranca declarativas (`config.yml`).
- **Input Rails (Inspecao Ativa de Entrada):**
  - Bloqueio imediato de ataques de Jailbreak (ex: *DAN mode*, *Do Anything Now*, desativacao de restricoes morais).
  - Bloqueio de Prompt Injection (ex: *Ignore all previous instructions*, comandos de substituicao de contexto, injecoes SQL).
  - Bloqueio de Engenharia Social (ex: usuarios fingindo ser gestores, donos ou autoridades exigindo descontos ou dados).
  - Tempo de resposta ultra-rapido (latencia inferior a 20ms na borda), abatendo o ataque antes de consumir tokens do LLM ou atingir o banco de dados.
- **Output Rails (Prevencao de Vazamento de Segredos e PII):**
  - Inspecao automatica de respostas contra padroes de chaves de API (`AQ.*`, `AIza*`, `sk-*`), strings de conexao com senhas de banco (`postgresql://`, `redis://`) e credenciais do servidor.
- **Trilha de Auditoria de Seguranca:**
  - Eventos de ataque sao sumariamente registrados em `audit_logs` sob o tipo `SECURITY_INPUT_RAIL_BLOCKED` para telemetria de ameacas em tempo real.

### Camada 8: Observabilidade, Auditoria Estruturada e Telemetria
- Emissao de logs estruturados em JSON contendo timestamp UTC em formato ISO8601, tenant ID, identificador do cliente, tempo de execucao em milissegundos e payload detalhado de entrada e saida.
- Persistencia relacional em `audit_logs` para conformidade e rastreabilidade historica.
- Endpoint administrativo dedicado `GET /audit/logs/{company_id_or_instance}` para consumo de trilhas de auditoria.

### Camada 8: Test Harness Deterministico (Offline, Custo R$ 0,00)
- Suite de testes automatizada via Pytest com execucao 100% isolada e sem consumo de chamadas externas de rede ou cobranca de tokens.
- O banco PostgreSQL e substituido em tempo de execucao por `sqlite+aiosqlite:///:memory:`.
- O servidor Redis e substituido por `fakeredis.aioredis.FakeRedis`.
- Modelos LLM sao substituidos por dubles de teste e mocks deterministas, validando 13 cenarios criticos de negocio em menos de 2 segundos.

---

## 3. Estrutura de Diretorios do Projeto

```text
.
├── pyproject.toml              # Gerenciador de dependencias uv e configuracoes do pytest
├── .env.example                # Especificacao das variaveis de ambiente obrigatorias
├── .gitignore                  # Regras de exclusao de credenciais, caches e dados locais
├── Dockerfile                  # Construcao multi-stage de imagem de producao
├── docker-compose.yml          # Orquestracao dos servicos (API, Postgres, Redis, Evolution)
├── app/
│   ├── __init__.py
│   ├── config.py               # Validacao tipada de configuracoes via Pydantic Settings
│   ├── database.py             # Conexoes assincronas, locks distribuidos e deduplicacao
│   ├── models.py               # Modelos relacionais e vetoriais SQLAlchemy 2.0
│   ├── schemas.py              # Contratos Pydantic e payloads da Evolution API v2
│   ├── logging_config.py       # Sistema de logs estruturados e auditoria em JSON
│   ├── rag.py                  # Servico de busca vetorial, sliding window e prompt injection
│   ├── vision.py               # Extracao multimodal de comprovantes bancarios via Gemini Vision
│   ├── graph.py                # Maquina de estados e nos cognitivos via LangGraph
│   ├── init_db.py              # Script idempotente de criacao de tabelas e sementes (seed)
│   └── main.py                 # Aplicacao FastAPI, endpoints de webhook e auditoria
├── scripts/
│   └── setup_evolution.py      # Automacao de criacao de instancias e pareamento via QR Code
└── tests/
    ├── __init__.py
    └── test_harness.py         # Suite completa de testes deterministicos offline
```

---

## 4. Variaveis de Ambiente

Crie o arquivo `.env` na raiz do projeto com base no arquivo `.env.example`:

```bash
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=whatsapp_engine
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
REDIS_URL=redis://localhost:6379/0
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=secret-token
GEMINI_API_KEY=sua_chave_gemini_aqui
```

---

## 5. Instrucoes de Execucao e Implantação

### 5.1. Inicializacao dos Servicos com Docker Compose

Suba a infraestrutura completa contendo o banco de dados PostgreSQL com pgvector, Redis, Evolution API v2 e a aplicacao FastAPI:

```bash
docker compose up -d --build
```

Verifique a saude dos containers:

```bash
docker compose ps
```

### 5.2. Populacao do Banco de Dados e Catalogo (Seed)

Execute o script de inicializacao idempotente para criar as extensoes, schemas, tabelas e carregar o catalogo de conhecimento RAG:

```bash
docker exec ia_wpp_api python -m app.init_db
```

### 5.3. Pareamento do WhatsApp (QR Code)

Para conectar o numero de WhatsApp a instancia do gateway Evolution API, execute o script de automacao:

```bash
uv run python scripts/setup_evolution.py
```

O script criara a instancia caso necessario, configurara os webhooks para apontar para `http://api:8000/webhook/evolution`, renderizara o QR Code em blocos ASCII no terminal e salvara a imagem fisica em `qrcode.png`.

---

## 6. Execucao da Suite de Testes

Para executar os 13 testes deterministicos offline com relatorio detalhado:

```bash
uv run pytest -v
```

Exemplo de saida:

```text
tests/test_harness.py::test_tenant_resolution_and_isolation PASSED       [  7%]
tests/test_harness.py::test_delivery_order_full_lifecycle PASSED         [ 15%]
tests/test_harness.py::test_human_handoff_trigger PASSED                 [ 23%]
tests/test_harness.py::test_redis_deduplication PASSED                   [ 30%]
tests/test_harness.py::test_lock_contention PASSED                       [ 38%]
tests/test_harness.py::test_material_construcao_rag_response PASSED      [ 46%]
tests/test_harness.py::test_outbound_and_group_filtering PASSED          [ 53%]
tests/test_harness.py::test_pharmacy_rag_search PASSED                   [ 61%]
tests/test_harness.py::test_sliding_window_memory PASSED                 [ 69%]
tests/test_harness.py::test_receipt_vision_audit PASSED                  [ 76%]
tests/test_harness.py::test_memory_tenant_isolation PASSED               [ 84%]
tests/test_harness.py::test_dynamic_context_injection PASSED             [ 92%]
tests/test_harness.py::test_ai_reasoning_node_mocked PASSED              [100%]

============================= 13 passed in 1.67s ==============================
```

---

## 7. Especificacao dos Endpoints

| Metodo | Rota | Descricao |
| :--- | :--- | :--- |
| `GET` | `/health` | Verificacao de disponibilidade e integridade do servico. |
| `POST` | `/webhook/evolution` | Endpoint receptor de eventos de mensageria da Evolution API v2 com deduplicacao, locks e orquestracao LangGraph. |
| `GET` | `/audit/logs/{company_id}` | Consulta da trilha de auditoria e telemetria filtrada estritamente por tenant. |
| `GET` | `/docs` | Documentacao interativa OpenAPI / Swagger UI. |

---

## 8. Licenca e Autoria

Projeto concebido para operacoes de alta demanda, arquitetado sob rigorosos padroes de engenharia de software para microsservicos corporativos assincronos.
