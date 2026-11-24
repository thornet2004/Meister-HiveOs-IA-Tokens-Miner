#!/usr/bin/env bash
# Incluído pelo agente do Hive: tem de definir $khs e $stats.
# O «hashrate» de cada placa são os tokens por segundo (média) do servidor a que ela pertence;
# num servidor repartido por várias placas, o valor é dividido por elas, para a soma ficar certa.
_dir=/hive/miners/custom/meisterllm
. $_dir/h-manifest.conf

_hs=() _temp=() _fan=() _bus=() _total=0
unset _val; declare -A _val
_auth=()
[[ -s $_dir/run/key ]] && _auth=(-H "Authorization: Bearer $(cat $_dir/run/key)")
if [[ -s $_dir/run/servers ]]; then
	while read -r _port _pid _gpus _name; do
		# Tokens gerados no último minuto, por segundo: o ritmo de trabalho da placa, como um hashrate médio.
		# O servidor só soma os tokens no fim de cada resposta, por isso uma leitura a 10 s dava zeros e picos falsos;
		# guarda-se um minuto de leituras e mede-se da mais antiga à mais nova. Parado, dá zero.
		_c=$(curl -s -m 1 "${_auth[@]}" "http://127.0.0.1:$_port/metrics" | awk '/^llamacpp:tokens_predicted_total /{print $2}')
		[[ $_c =~ ^[0-9.]+$ ]] || _c=0
		_now=$(date +%s) _h="$_dir/run/stats.$_port"
		echo "$_now $_c" >> "$_h"
		_v=$(awk -v now="$_now" -v out="$_h.new" '$1 >= now - 60 && NF == 2 { print > out; if (!n++) { t0 = $1; c0 = $2 } t1 = $1; c1 = $2 }
			END { print (n > 1 && t1 > t0 && c1 >= c0) ? (c1 - c0) / (t1 - t0) : 0 }' "$_h")
		mv -f "$_h.new" "$_h" 2>/dev/null
		_total=$(awk -v a="$_total" -v b="$_v" 'BEGIN{print a+b}')
		_n=$(awk -F, '{print NF}' <<< "$_gpus")
		for _gpu in ${_gpus//,/ }; do _val[$_gpu]=$(awk -v v="$_v" -v n="$_n" 'BEGIN{printf "%.2f", v/n}'); done
	done < $_dir/run/servers
fi
# Placa do Whisper: segundos de fala transcritos no último minuto (5 s de fala = 5). Não é somado ao total,
# que são tokens por segundo.
if read -r _port _pid _gpu _name 2>/dev/null < $_dir/run/whisper; then
	_c=$(curl -s -m 1 "http://127.0.0.1:$_port/stats" | jq -r '.audio_seconds // 0' 2>/dev/null)
	[[ $_c =~ ^[0-9.]+$ ]] || _c=0
	_now=$(date +%s) _h="$_dir/run/stats.$_port"
	echo "$_now $_c" >> "$_h"
	_val[$_gpu]=$(awk -v now="$_now" -v out="$_h.new" '$1 >= now - 60 && NF == 2 { print > out; if (!n++) c0 = $2; c1 = $2 }
		END { printf "%.2f", (n > 1 && c1 >= c0) ? c1 - c0 : 0 }' "$_h")
	mv -f "$_h.new" "$_h" 2>/dev/null
fi
# Todas as placas entram na lista, pela ordem do sistema: uma placa sem trabalho fica com zero em vez de
# desaparecer do painel.
while IFS=', ' read -r _gpu _t _f _b; do
	_hs+=("${_val[$_gpu]:-0}") _temp+=("${_t:-0}") _fan+=("${_f:-0}") _bus+=($((${_b:-0})))
done < <(nvidia-smi --query-gpu=index,temperature.gpu,fan.speed,pci.bus --format=csv,noheader,nounits)
_up=0
[[ -s $_dir/run/started ]] && _up=$(($(date +%s) - $(cat $_dir/run/started)))
_arr() { local IFS=,; echo "[$*]"; }

khs=$(awk -v t="$_total" 'BEGIN{print t/1000}')
stats=$(jq -nc --argjson hs "$(_arr "${_hs[@]}")" --argjson temp "$(_arr "${_temp[@]}")" --argjson fan "$(_arr "${_fan[@]}")" \
	--argjson bus "$(_arr "${_bus[@]}")" --arg up "$_up" --arg ver "$CUSTOM_VERSION" \
	'{hs: $hs, hs_units: "hs", temp: $temp, fan: $fan, uptime: ($up|tonumber), ver: $ver, ar: [0, 0], algo: "tokens", bus_numbers: $bus}')
