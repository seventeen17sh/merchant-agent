#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
chmod 600 .env
open -a TextEdit .env
