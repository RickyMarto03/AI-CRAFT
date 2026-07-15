#!/bin/bash
# Doppio click su questo file per avviare AI-craft.
# (Se macOS si lamenta la prima volta: tasto destro -> Apri, poi conferma.)

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Prima volta: creo l'ambiente Python e installo le dipendenze..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
else
  source .venv/bin/activate
fi

echo "Avvio AI-craft..."
python -m aicraft.desktop.app

echo ""
echo "--- L'app si e' chiusa. Premi Invio per chiudere questa finestra. ---"
read -r
