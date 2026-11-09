#!/usr/bin/env bash
# ==============================================================================
# ApplyPilot — Script d'installation automatique sur VPS (Ubuntu 22.04 / 24.04 / Debian)
# Usage : bash deploy/setup_vps.sh
# ==============================================================================

set -euo pipefail

echo "=========================================================="
echo "🚀 Installation d'ApplyPilot sur VPS (Ubuntu / Debian)"
echo "=========================================================="

# 1. Mise à jour des paquets système
echo "📦 Mise à jour du système..."
sudo apt-get update -y
sudo apt-get install -y \
    curl \
    git \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    sqlite3

# 2. Installation de TeX Live pour la compilation des CV LaTeX
echo "📄 Installation de LaTeX (TeX Live pour génération des CV)..."
sudo apt-get install -y --no-install-recommends \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-lang-french \
    latexmk

# 3. Préparation du dossier de configuration
echo "📁 Préparation de ~/.applypilot..."
mkdir -p "$HOME/.applypilot/cv_latex"

# 4. Création du venv Python
echo "🐍 Création de l'environnement virtuel Python..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .

# 5. Configuration du service systemd
echo "⚙️ Configuration du service systemd..."
CURRENT_USER=$(whoami)
CURRENT_DIR=$(pwd)

# Adapte le service avec le bon utilisateur et chemin
sed -e "s|User=ubuntu|User=$CURRENT_USER|g" \
    -e "s|WorkingDirectory=/home/ubuntu/applypilot-fr|WorkingDirectory=$CURRENT_DIR|g" \
    -e "s|/home/ubuntu/applypilot-fr/.venv/bin/python|$CURRENT_DIR/.venv/bin/python|g" \
    deploy/applypilot.service | sudo tee /etc/systemd/system/applypilot.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable applypilot

echo "=========================================================="
echo "✅ Installation terminée avec succès !"
echo ""
echo "👉 Prochaines étapes :"
echo "1. Copie tes fichiers secrets depuis ton PC vers le VPS :"
echo "   - ~/.applypilot/.env"
echo "   - ~/.applypilot/experiences.json"
echo "   - ~/.applypilot/applypilot.db"
echo "   - ~/.applypilot/token.json (si configuré pour Gmail)"
echo ""
echo "2. Démarre le bot :"
echo "   sudo systemctl start applypilot"
echo ""
echo "3. Vérifie les logs en direct :"
echo "   journalctl -u applypilot -f"
echo "=========================================================="
