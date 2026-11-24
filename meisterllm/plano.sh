#!/usr/bin/env bash
# Plano automático: reparte um modelo pelas placas de uma rig que nunca foi afinada à mão.
# Uso: plano.sh <tamanho do modelo em MiB> [contexto máximo]      Entrada: linhas «índice, MiB da placa» (como dá o nvidia-smi).
# Saída: uma linha por servidor — «placas|contexto|argumentos». Sai com erro se nem um servidor couber.
# As contas são prudentes de propósito: preferem um contexto mais pequeno a um servidor que não arranca.
#   por placa: 1200 MiB de folga (o motor, o ecrã do Hive e o que não se consegue encher até ao fim);
#   por servidor: 2000 MiB para o contexto de 16 384; com mais 3000 MiB livres sobe para 32 768.
# Medido na rig de origem (6 × 8 GB, modelo de 15,8 GiB): três placas chegam, como este plano dá.
size=$1 maxctx=${2:-32768}
[[ $size =~ ^[0-9]+$ && $size -gt 0 ]] || { echo "tamanho do modelo inválido: $size" >&2; exit 2; }
PER=1200 BASE=2000 ROOM=3000
group='' n=0 sum=0 out=0
while IFS=', ' read -r idx mem _; do
	[[ $idx =~ ^[0-9]+$ && $mem =~ ^[0-9]+$ ]] || continue
	group=${group:+$group,}$idx; n=$((n + 1)); sum=$((sum + mem))
	need=$((size + PER * n + BASE))
	if ((sum >= need)); then
		ctx=16384
		((sum >= need + ROOM)) && ctx=32768
		((ctx > maxctx)) && ctx=$maxctx
		args=''
		# Em várias placas, a memória do contexto vai comprimida (q8): gasta metade e não se nota na resposta.
		((n > 1)) && args='-fa on -ctk q8_0 -ctv q8_0'
		echo "$group|$ctx|$args"
		out=$((out + 1)); group='' n=0 sum=0
	fi
done
[[ -n $group ]] && echo "sobram|$group|$sum" >&2
((out > 0))
