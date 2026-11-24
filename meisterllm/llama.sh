#!/bin/bash
# Arranca um programa do llama.cpp com as bibliotecas do Ubuntu 24.04 guardadas ao lado (o HiveOS traz a glibc 2.35).
# Corre a partir da pasta do motor: lançado pelo carregador, ele só encontra os backends (CUDA) na pasta atual.
# Por isso os caminhos passados (modelos, registos) têm de ser absolutos.
R="$(cd "$(dirname "$0")" && pwd)"; N="$R/noble/usr/lib/x86_64-linux-gnu"; L="$R/llama/llama-b12651"
P="$1"; shift; cd "$L"
exec "$N/ld-linux-x86-64.so.2" --library-path "$N:$L:$R/llama/cudart-llama-b12651-bin-ubuntu-cuda-12.8-x64:/usr/lib/x86_64-linux-gnu:/lib/x86_64-linux-gnu" "$L/$P" "$@"
