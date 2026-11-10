#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE BOOTSTRAP, HARDENING E PREPARAÇÃO DE VPS (UBUNTU 22.04 / 24.04 LTS)
# ==============================================================================
# Uso: sudo bash scripts/deploy_vps.sh
# ==============================================================================

set -euo pipefail

echo "======================================================================"
echo ">>> INICIANDO SETUP E HARDENING DA VPS PARA PRODUÇÃO"
echo "======================================================================"

# 1. Verificação de privilégios de superusuário (root)
if [[ $EUID -ne 0 ]]; then
   echo "Erro: Este script deve ser executado como root (sudo)." >&2
   exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# 2. Atualização de pacotes do sistema operacional
echo ">>> [1/7] Atualizando lista e pacotes do sistema..."
apt-get update -y
apt-get upgrade -y

# 3. Instalação de utilitários fundamentais
echo ">>> [2/7] Instalando utilitários essenciais..."
apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    ufw \
    fail2ban \
    htop \
    jq \
    tar \
    unzip

# 4. Configuração de Swap de 4GB (Proteção contra OOM em concorrência)
echo ">>> [3/7] Configurando memória Swap (4GB)..."
if ! grep -q '/swapfile' /etc/fstab; then
    fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    # Ajuste fino de swappiness para bancos e containers
    sysctl vm.swappiness=10
    echo 'vm.swappiness=10' >> /etc/sysctl.d/99-swappiness.conf
    echo "Swap configurado com sucesso."
else
    echo "Swap já configurado previamente. Pulando."
fi

# 5. Instalação oficial do Docker Engine e Docker Compose Plugin
echo ">>> [4/7] Instalando Docker Engine oficial e Compose Plugin..."
if ! command -v docker &> /dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    systemctl enable docker
    systemctl start docker
    echo "Docker instalado com sucesso: $(docker --version)"
else
    echo "Docker já instalado: $(docker --version). Pulando."
fi

# 6. Hardening de Rede: Configuração do Firewall (UFW)
echo ">>> [5/7] Configurando regras estritas de Firewall (UFW)..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# Portas públicas estritamente permitidas
ufw allow 22/tcp comment 'SSH seguro'
ufw allow 80/tcp comment 'HTTP Caddy ACME'
ufw allow 443/tcp comment 'HTTPS Caddy SSL'

# Ativa o firewall sem derrubar sessão SSH ativa
ufw --force enable
ufw status verbose

# 7. Configuração do Fail2ban para proteção SSH contra força bruta
echo ">>> [6/7] Configurando proteção contra força bruta (fail2ban)..."
cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
cat << 'EOF' > /etc/fail2ban/jail.d/ssh-hardening.local
[sshd]
enabled = true
port = 22
filter = sshd
logpath = /var/log/auth.log
maxretry = 5
bantime = 1h
findtime = 10m
EOF
systemctl enable fail2ban
systemctl restart fail2ban

# 8. Criação da pasta de deploy oficial
echo ">>> [7/7] Preparando diretório de deploy (/opt/whatsapp-engine)..."
DEPLOY_DIR="/opt/whatsapp-engine"
mkdir -p "${DEPLOY_DIR}"
chmod 755 "${DEPLOY_DIR}"

echo "======================================================================"
echo ">>> BOOTSTRAP E HARDENING CONCLUÍDOS COM SUCESSO!"
echo ">>> Diretório de deploy: ${DEPLOY_DIR}"
echo ">>> Próximos passos:"
echo "    1. cd ${DEPLOY_DIR}"
echo "    2. Clone seu repositório ou copie os arquivos"
echo "    3. Configure o arquivo .env (baseado em .env.prod.example)"
echo "    4. Execute: bash scripts/start_prod.sh"
echo "======================================================================"
