#!/usr/bin/env bash
# Chamado pelo Hive a cada arranque: escreve a configuração do minerador.
# No «flight sheet»: «Pool URL» = endereço do modelo (.gguf); «Pass» = SHA-256 do modelo (opcional);
# «Extra config arguments» = linhas CHAVE=valor que substituem os valores por omissão abaixo.
# Sem linhas SERVERn, o modelo do «Pool URL» é repartido sozinho pelas placas (plano.sh), com contas prudentes.
# Para repartir à mão, uma linha por servidor (o primeiro fica na porta PORT, o segundo em PORT+1...):
#   SERVER1=0,1,2|modelo.gguf ou endereço|sha256|nome|contexto|argumentos     (do sha256 em diante pode ficar vazio)

[[ -z $CUSTOM_CONFIG_FILENAME ]] && . "$(dirname "${BASH_SOURCE[0]}")/h-manifest.conf"

{
	echo "# Gerado por h-config.sh; não editar à mão (o Hive volta a escrevê-lo)."
	echo "RIG_HOME=/home/user/meister-rig"   # motor, bibliotecas e modelos
	echo "GPUS=all"                           # «all» ou índices separados por espaço: «0 1 2»
	echo "PORT=8081"                          # a placa N responde na porta PORT+N
	echo "HOST=0.0.0.0"                       # só rede local: a rig não deve estar exposta à internet
	echo "CTX=32768"                          # contexto de cada servidor, em tokens (o máximo deste modelo; a app com o agente ligado manda ~10 mil)
	echo "PARALLEL=1"                         # pedidos em simultâneo por placa
	echo "RESET_CLOCKS=1"                     # repõe os relógios das placas (o overclock de mineração trava a geração)
	echo "CACHE_RAM=512"                      # MiB de RAM que cada servidor pode gastar a guardar pedidos antigos (o motor traz 8192: com 8 GB de RAM bloqueia a rig)
	echo "CHECKPOINTS=4"                      # cópias do contexto guardadas por servidor (o motor traz 32)
	echo "SLOTS="                             # MODO TROCA: grupos de placas, um modelo por grupo (ex.: «0,1,2 3,4,5»). Vazio = modo antigo (servidores sempre ligados).
	echo "QUENTES="                           # MODO TROCA: nomes dos modelos a manter quentes/pré-carregados (ex.: «qwen3.8-27b qwen3-coder-30b»).
	echo "KEEPALIVE=600"                      # MODO TROCA: segundos parado antes de descarregar um modelo não-quente (o próximo pedido recarrega-o).
	echo "VAGA_FIXA="                         # MODO TROCA: modelos presos a uma vaga (ex.: «qwen3-coder-30b:1») — p.ex. um contexto grande que não cabe na vaga do Whisper.
	echo "PC_IP="                             # IP do PC do dono, para pré-preencher o «Acesso remoto ao PC» na página mobile (ex.: «192.168.1.50»); vazio = o utilizador escreve-o.
	echo "SWAP=1"                             # liga a memória de reserva RIG_HOME/swapfile, se existir
	echo "WHISPER_GPU="                        # placa para a transcrição de voz (vazio = sem Whisper); precisa de ~1,2 GB livres nela
	echo "WHISPER_PORT=8089"
	echo "WHISPER_MODEL=large-v3-turbo"        # pasta em RIG_HOME/whisper/modelo
	echo "GATE_PORT=8080"                      # porteiro: porta única para todos os modelos e a voz, com chaves por amigo (vazio = sem porteiro)
	echo "GATE_PROXY="                         # endereço do proxy HTTPS que traz os pedidos de fora (ex.: 192.168.1.10)
	echo "GATE_FRIEND_PARALLEL=2"              # conversas em simultâneo por amigo
	echo "FIREWALL=1"                          # isola a rig da rede de casa (ver firewall.sh); 0 = não mexe na firewall
	echo "FIREWALL_ALLOW="                     # aparelhos da rede local a que a rig pode ligar-se por iniciativa dela (ex.: um armazém de modelos)
	echo "API_KEY="                           # chave exigida pelos servidores; vazia = usa a de RIG_HOME/api_key, se existir
	echo "EXTRA="                             # argumentos a mais para o llama-server
	echo "MODEL_URL=$(printf '%q' "${CUSTOM_URL}")"
	[[ ${CUSTOM_PASS} =~ ^[0-9a-fA-F]{64}$ ]] && echo "MODEL_SHA256=${CUSTOM_PASS}" || echo "MODEL_SHA256="
	# Só passam linhas simples CHAVE=valor, para o ficheiro poder ser lido pelo bash sem surpresas.
	while IFS= read -r line; do
		[[ $line =~ ^[A-Z_][A-Z0-9_]*=[A-Za-z0-9_./:\ @%+=,|?\&-]*$ ]] || continue
		echo "${line%%=*}=$(printf '%q' "${line#*=}")"
	done <<< "${CUSTOM_USER_CONFIG}"
} > "$CUSTOM_CONFIG_FILENAME"
