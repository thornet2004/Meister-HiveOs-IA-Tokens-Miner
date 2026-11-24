#!/usr/bin/env bash
# Arranca os servidores llama.cpp da rig e fica à espera (o Hive vigia este processo).
# Sem linhas SERVERn na configuração: o modelo do «Pool URL» é repartido sozinho pelas placas (plano.sh) — um servidor por
# placa se couber numa, senão um por cada grupo de placas que chegue para ele.
# Numa rig nova não é preciso preparar nada: o motor instala-se no primeiro arranque e a chave de dono nasce aqui.
# Com linhas SERVERn=placas|modelo|sha256|nome|contexto|argumentos: um servidor por linha, repartido pelas placas indicadas;
# «argumentos» são opções a mais só para esse servidor (ex.: --spec-type draft-mtp).
cd "$(dirname "$0")" || exit 1
. ./h-manifest.conf
[[ -f $CUSTOM_CONFIG_FILENAME ]] || ./h-config.sh
. "$CUSTOM_CONFIG_FILENAME"

LOG="${CUSTOM_LOG_BASENAME}.log"
mkdir -p "$(dirname "$LOG")" run "$RIG_HOME/modelos"
say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
fail() { say "$*"; sleep 30; exit 1; }

# Chave de acesso: a do flight sheet ou, na falta dela, a guardada na rig (não passa pelo painel do Hive).
# Rig nova, sem nenhuma: cria-se aqui a chave de dono. Não vai para o registo; o dono lê-a no ficheiro.
if [[ -z $API_KEY && ! -s $RIG_HOME/api_key ]]; then
	(umask 077; python3 -c "import secrets; print('mr-dono-' + secrets.token_urlsafe(32))" > "$RIG_HOME/api_key") \
		&& say "Criei a chave de dono desta rig. Para a ver, na consola da rig: cat $RIG_HOME/api_key"
fi
[[ -z $API_KEY && -s $RIG_HOME/api_key ]] && API_KEY=$(tr -d '[:space:]' < "$RIG_HOME/api_key")

# Memória de reserva: sem ela, quando a RAM acaba a rig fica presa em vez de abrandar (aconteceu a 23/11/2026).
if [[ $SWAP == 1 && -f $RIG_HOME/swapfile ]] && ! swapon --show=NAME --noheadings | grep -qx "$RIG_HOME/swapfile"; then
	swapon "$RIG_HOME/swapfile" && say "Memória de reserva ligada." || say "Não consegui ligar a memória de reserva."
fi

# Primeira vez nesta rig (ou Whisper pedido e ainda por instalar): prepara-se sozinha. O instalar.sh só faz o que falta.
want_whisper=; [[ -n $WHISPER_GPU && ! -d $RIG_HOME/whisper/site/faster_whisper ]] && want_whisper=1
if [[ ! -x $RIG_HOME/llama.sh || ! -d $RIG_HOME/noble || -n $want_whisper ]]; then
	say "A preparar a rig em $RIG_HOME (motor${want_whisper:+ e Whisper}): só acontece uma vez e demora alguns minutos."
	say "Espaço livre no disco: $(df -BG --output=avail "$RIG_HOME" | tail -1 | tr -dc 0-9) GB."
	INSTALAR_WHISPER=$want_whisper RIG_HOME=$RIG_HOME ./instalar.sh >> "$LOG" 2>&1 || say "A preparação não chegou ao fim; ver $LOG"
fi
[[ -x $RIG_HOME/llama.sh && -d $RIG_HOME/noble ]] || fail "Falta o motor em $RIG_HOME. Vê o erro acima ou corre à mão: $(pwd)/instalar.sh"

# Modelo: endereço (descarrega-se uma vez e fica em cache) ou nome de um ficheiro já em modelos/.
# Com SHA-256, confere-se uma vez por ficheiro. Deixa o caminho em $FILE.
fetch() {
	local spec=$1 sha=${2,,}
	FILE="$RIG_HOME/modelos/$(basename "${spec%%\?*}")"
	if [[ ! -s $FILE ]]; then
		[[ $spec == http://* || $spec == https://* ]] || { say "Modelo em falta e sem endereço válido: $spec"; return 1; }
		say "A descarregar o modelo: $spec"
		wget -q -c -O "$FILE.part" "$spec" && mv "$FILE.part" "$FILE" || { say "A descarga falhou: $spec"; return 1; }
		rm -f "$FILE.ok"
	fi
	if [[ -n $sha && ! -f $FILE.ok ]]; then
		[[ $(sha256sum "$FILE" | cut -d' ' -f1) == "$sha" ]] || { say "SHA-256 não bate certo: $FILE (não o uso)."; return 1; }
		touch "$FILE.ok"
	fi
}

# Tabela de servidores: placas, ficheiro do modelo, nome, contexto.
S_GPUS=() S_FILE=() S_NAME=() S_CTX=() S_ARGS=()
add() {  # placas modelo sha nome contexto argumentos
	fetch "$2" "$3" || return 1
	local name=$4
	[[ -n $name ]] || { name=$(basename "$FILE"); name=${name%.gguf}; }
	S_GPUS+=("${1// /}") S_FILE+=("$FILE") S_NAME+=("$name") S_CTX+=("${5:-$CTX}") S_ARGS+=("$6")
}
for var in $(compgen -v | grep -E '^SERVER[0-9]+$' | sort -V); do
	IFS='|' read -r g m sha name ctx more <<< "${!var}"
	[[ -n $g && -n $m ]] || { say "$var mal escrito; esperado placas|modelo|sha256|nome|contexto."; continue; }
	add "$g" "$m" "$sha" "$name" "$ctx" "$more" || say "$var fica de fora."
done
if [[ ${#S_GPUS[@]} -eq 0 ]]; then
	[[ -n $MODEL_URL ]] || fail "Falta o modelo: «Pool URL» no flight sheet ou linhas SERVERn em «Extra config arguments»."
	fetch "$MODEL_URL" "$MODEL_SHA256" || fail "Sem modelo utilizável."
	size=$(($(stat -c %s "$FILE") / 1048576)); base=$(basename "$FILE"); base=${base%.gguf}
	cards=$(nvidia-smi --query-gpu=index,memory.total --format=csv,noheader,nounits)
	[[ $GPUS == all ]] || cards=$(grep -E "^(${GPUS// /|})," <<< "$cards")
	plan=$(./plano.sh "$size" "$CTX" <<< "$cards" 2> run/plano.sobras) \
		|| fail "O modelo ($size MiB) não cabe nas placas desta rig, nem todas juntas. Põe um modelo mais pequeno no «Pool URL»."
	k=0
	while IFS='|' read -r g ctx more; do
		k=$((k + 1)); name=$base; [[ $k -gt 1 ]] && name=$base-$k  # nomes diferentes: o porteiro escolhe o servidor pelo nome
		add "$g" "$MODEL_URL" "$MODEL_SHA256" "$name" "$ctx" "$more" && say "Plano automático: «$name» nas placas $g, contexto $ctx."
	done <<< "$plan"
	IFS='|' read -r _ left _ < run/plano.sobras && [[ -n $left ]] && say "Placas sem uso neste plano (não chegam para mais um servidor): $left."
fi
[[ ${#S_GPUS[@]} -gt 0 ]] || fail "Nenhum servidor para arrancar."

pid=() falls=() gpid= gfalls=0
table() { local j; : > run/servers; for j in "${!S_GPUS[@]}"; do echo "$((PORT + j)) ${pid[$j]:-0} ${S_GPUS[$j]} ${S_NAME[$j]}" >> run/servers; done; }
stop() { say "A parar os servidores."; kill "${pid[@]}" $wpid $gpid 2>/dev/null; wait; rm -f run/servers run/whisper; exit 0; }
trap stop INT TERM

start() {  # arranca o servidor número $1 e espera que fique pronto
	local i=$1 port=$((PORT + $1)) gpu
	if [[ $RESET_CLOCKS == 1 ]]; then
		for gpu in ${S_GPUS[$i]//,/ }; do
			nvidia-smi -i "$gpu" -rgc > /dev/null 2>&1
			nvidia-smi -i "$gpu" -rmc > /dev/null 2>&1
			# O overclock do Hive também soma MHz ao núcleo; sem o bloqueio de relógio isso rebenta placas mais fracas (Xid 13).
			DISPLAY=${DISPLAY:-:0} nvidia-settings -a "[gpu:$gpu]/GPUGraphicsClockOffsetAllPerformanceLevels=0" \
				-a "[gpu:$gpu]/GPUMemoryTransferRateOffsetAllPerformanceLevels=0" > /dev/null 2>&1
		done
	fi
	local args=(-m "${S_FILE[$i]}" --alias "${S_NAME[$i]}" -ngl 99 -c "${S_CTX[$i]}" -np "$PARALLEL" --host "$HOST" --port "$port" --metrics --cache-ram "${CACHE_RAM:-512}" --ctx-checkpoints "${CHECKPOINTS:-4}")
	[[ -n $API_KEY ]] && args+=(--api-key "$API_KEY")
	[[ -s run/s$i.log ]] && mv -f "run/s$i.log" "run/s$i.anterior.log"
	# shellcheck disable=SC2086
	CUDA_VISIBLE_DEVICES=${S_GPUS[$i]} "$RIG_HOME/llama.sh" llama-server "${args[@]}" $EXTRA ${S_ARGS[$i]} > "run/s$i.log" 2>&1 &
	pid[$i]=$!
	table
	# Um de cada vez: com dois núcleos, carregar vários modelos em simultâneo só atrasa todos.
	for _ in $(seq 1 600); do
		curl -s -m 2 "http://127.0.0.1:$port/health" | grep -q '"ok"' && break
		kill -0 "${pid[$i]}" 2>/dev/null || break
		sleep 1
	done
	if kill -0 "${pid[$i]}" 2>/dev/null; then say "${S_NAME[$i]} pronto na porta $port (placas ${S_GPUS[$i]})."
	else say "${S_NAME[$i]} não arrancou (placas ${S_GPUS[$i]}); ver $(pwd)/run/s$i.log"; fi
}

# Transcrição de voz (Whisper) numa placa, ao lado de um modelo de texto. Só arranca se WHISPER_GPU estiver definido
# e o motor existir em RIG_HOME/whisper (ver instalar.sh). Usa a mesma chave dos outros servidores.
wpid= wfalls=0
whisper() {
	local w=$RIG_HOME/whisper model=$RIG_HOME/whisper/modelo/${WHISPER_MODEL:-large-v3-turbo}
	[[ -n $WHISPER_GPU ]] || return 0
	[[ -d $w/site/faster_whisper && -s $model/model.bin ]] || { say "Whisper pedido mas não instalado em $w."; WHISPER_GPU=; return 0; }
	pkill -f 'whisper_server\.py' 2>/dev/null && sleep 1  # um só Whisper por rig: tira algum que tenha ficado de um arranque à mão
	[[ -s run/whisper.log ]] && mv -f run/whisper.log run/whisper.anterior.log
	CUDA_VISIBLE_DEVICES=$WHISPER_GPU PYTHONPATH=$w/site LD_LIBRARY_PATH=$w/site/nvidia/cudnn/lib:$w/site/nvidia/cublas/lib WHISPER_KEY=$API_KEY \
		python3 "$(pwd)/whisper_server.py" "$model" "${WHISPER_PORT:-8089}" "$HOST" "${WHISPER_COMPUTE:-int8_float16}" > run/whisper.log 2>&1 &
	wpid=$!
	echo "${WHISPER_PORT:-8089} $wpid $WHISPER_GPU ${WHISPER_MODEL:-large-v3-turbo}" > run/whisper
	for _ in $(seq 1 120); do
		curl -s -m 2 "http://127.0.0.1:${WHISPER_PORT:-8089}/health" | grep -q '"ok"' && break
		kill -0 "$wpid" 2>/dev/null || break
		sleep 1
	done
	if kill -0 "$wpid" 2>/dev/null; then say "Whisper pronto na porta ${WHISPER_PORT:-8089} (placa $WHISPER_GPU)."
	else say "Whisper não arrancou (placa $WHISPER_GPU); ver $(pwd)/run/whisper.log"; fi
}

# Porteiro: a porta única (modelos e voz) onde vivem as chaves dos amigos. É o que se põe atrás do proxy HTTPS.
gate() {
	[[ -n $GATE_PORT ]] || return 0
	local old
	for old in $(pgrep -f 'porteiro\.py'); do kill "$old" 2>/dev/null; done  # um só porteiro: tira algum arrancado à mão
	[[ -s run/porteiro.log ]] && mv -f run/porteiro.log run/porteiro.anterior.log
	RIG_HOME=$RIG_HOME GATE_PROXY=$GATE_PROXY GATE_FRIEND_PARALLEL=$GATE_FRIEND_PARALLEL python3 "$(pwd)/porteiro.py" "$GATE_PORT" "$HOST" "$(pwd)/run" > run/porteiro.log 2>&1 &
	gpid=$!
	sleep 1
	if kill -0 "$gpid" 2>/dev/null; then say "Porteiro pronto na porta $GATE_PORT."
	else say "O porteiro não arrancou; ver $(pwd)/run/porteiro.log"; fi
}

date +%s > run/started
(umask 077; printf '%s' "$API_KEY" > run/key)  # para o h-stats.sh ler as métricas
[[ ${FIREWALL:-1} == 1 ]] && say "$(./firewall.sh on 2>&1 | tail -1)"
gate
for i in "${!S_GPUS[@]}"; do start "$i"; done
whisper

# Vigia: um servidor que caia é reposto (até 5 vezes); se nenhum estiver vivo, sai para o Hive reiniciar tudo.
while :; do
	alive=0
	for i in "${!S_GPUS[@]}"; do
		if kill -0 "${pid[$i]}" 2>/dev/null; then alive=$((alive + 1)); continue; fi
		falls[$i]=$((${falls[$i]:-0} + 1))
		if [[ ${falls[$i]} -le 5 ]]; then
			say "${S_NAME[$i]} (placas ${S_GPUS[$i]}) caiu (${falls[$i]}.ª vez); registo em run/s$i.anterior.log. A repor."
			start "$i"
			kill -0 "${pid[$i]}" 2>/dev/null && alive=$((alive + 1))
		elif [[ ${falls[$i]} -eq 6 ]]; then
			say "${S_NAME[$i]} caiu demasiadas vezes; fica de fora até ao próximo arranque."
		fi
	done
	[[ $alive -gt 0 ]] || { say "Nenhum servidor vivo."; kill $wpid $gpid 2>/dev/null; rm -f run/servers run/whisper; exit 1; }
	if [[ -n $WHISPER_GPU && -n $wpid ]] && ! kill -0 "$wpid" 2>/dev/null && [[ $wfalls -lt 5 ]]; then
		wfalls=$((wfalls + 1)); say "Whisper caiu ($wfalls.ª vez). A repor."; whisper
	fi
	if [[ -n $GATE_PORT && -n $gpid ]] && ! kill -0 "$gpid" 2>/dev/null && [[ $gfalls -lt 5 ]]; then
		gfalls=$((gfalls + 1)); say "O porteiro caiu ($gfalls.ª vez). A repor."; gate
	fi
	sleep 10 & wait $!
done
