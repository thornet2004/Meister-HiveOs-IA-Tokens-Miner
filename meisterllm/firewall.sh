#!/usr/bin/env bash
# Isola a rig do resto da rede de casa, para o caso de alguém conseguir entrar nela pelo porteiro:
#   - a rig deixa de poder INICIAR ligações para os outros aparelhos da rede local (só responde a quem a chama
#     e fala com o router e com a internet, de que o Hive precisa);
#   - o proxy HTTPS (GATE_PROXY) só chega à porta do porteiro; SSH, modelos, Whisper e o resto ficam-lhe fechados.
# O resto da rede de casa continua a chegar à rig como antes (SSH, portas dos modelos, página /admin).
# As regras vivem em cadeias próprias (MEISTER_IN, MEISTER_OUT) e não sobrevivem a um reinício: o h-run.sh repõe-nas.
# Uso: firewall.sh on|off|status
cd "$(dirname "$0")" || exit 1
. ./h-manifest.conf
[[ -f $CUSTOM_CONFIG_FILENAME ]] && . "$CUSTOM_CONFIG_FILENAME"

# O núcleo do HiveOS não aceita as regras pela variante nova do iptables (nf_tables): usa-se a clássica, se existir.
IPT=iptables IPT6=ip6tables
command -v iptables-legacy > /dev/null && IPT=iptables-legacy
command -v ip6tables-legacy > /dev/null && IPT6=ip6tables-legacy
[[ -e /proc/net/if_inet6 ]] || IPT6=  # núcleo sem IPv6 (é o caso do HiveOS desta rig): não há nada a fechar por aí

DEV=$(ip -4 route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')
GW=$(ip -4 route show default | awk '{print $3; exit}')
NET=$(ip -4 route show dev "$DEV" scope link 2>/dev/null | awk '$1 ~ /\// {print $1; exit}')

off() {
	local t
	for t in $IPT $IPT6; do
		while $t -D OUTPUT -j MEISTER_OUT 2>/dev/null; do :; done
		while $t -D INPUT -j MEISTER_IN 2>/dev/null; do :; done
		$t -F MEISTER_OUT 2>/dev/null; $t -X MEISTER_OUT 2>/dev/null
		$t -F MEISTER_IN 2>/dev/null; $t -X MEISTER_IN 2>/dev/null
	done
}

on() {
	[[ -n $DEV && -n $GW && -n $NET ]] || { echo "Firewall: não percebi a rede (placa «$DEV», router «$GW», rede «$NET»); não mexo em nada."; return 1; }
	local t ip
	off
	modprobe -a ip_tables iptable_filter ip6_tables ip6table_filter nf_conntrack xt_conntrack 2>/dev/null
	for t in $IPT $IPT6; do
		$t -N MEISTER_OUT && $t -N MEISTER_IN || return 1
		$t -A MEISTER_OUT -o lo -j ACCEPT
		$t -A MEISTER_OUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
		# Respostas TCP passam sempre (só o primeiro pacote de uma ligação nova, o SYN, é que fica sujeito às regras):
		# assim uma sessão de SSH já aberta não fica pendurada à espera de o registo de ligações a reconhecer (aconteceu a 23/11).
		$t -A MEISTER_OUT -p tcp ! --syn -j ACCEPT
		$t -I OUTPUT 1 -j MEISTER_OUT && $t -I INPUT 1 -j MEISTER_IN || return 1
	done
	$IPT -A MEISTER_OUT -d "$GW" -j ACCEPT
	for ip in ${FIREWALL_ALLOW//,/ }; do $IPT -A MEISTER_OUT -d "$ip" -j ACCEPT; done
	$IPT -A MEISTER_OUT -d "$NET" -j REJECT
	if [[ -n $IPT6 ]]; then  # IPv6: fica o que a rede precisa para funcionar e mais nada para os vizinhos
		$IPT6 -A MEISTER_OUT -p ipv6-icmp -j ACCEPT
		$IPT6 -A MEISTER_OUT -p udp --dport 547 -j ACCEPT
		$IPT6 -A MEISTER_OUT -d fe80::/10 -j REJECT
		$IPT6 -A MEISTER_OUT -d fc00::/7 -j REJECT
	fi
	for ip in ${GATE_PROXY//,/ }; do
		[[ $ip =~ ^[0-9.]+$ ]] || continue
		$IPT -A MEISTER_IN -s "$ip" -p tcp --dport "${GATE_PORT:-8080}" -j ACCEPT
		$IPT -A MEISTER_IN -s "$ip" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
		$IPT -A MEISTER_IN -s "$ip" -j DROP
	done
	echo "Firewall ligada: a rig não inicia ligações para $NET (menos o router $GW${FIREWALL_ALLOW:+ e $FIREWALL_ALLOW})${GATE_PROXY:+; $GATE_PROXY só chega à porta ${GATE_PORT:-8080}}."
}

case $1 in
	on) on ;;
	off) off; echo "Firewall desligada." ;;
	status) $IPT -S MEISTER_OUT 2>/dev/null; $IPT -S MEISTER_IN 2>/dev/null; [[ -n $IPT6 ]] && $IPT6 -S MEISTER_OUT 2>/dev/null; $IPT -S OUTPUT | grep -q MEISTER_OUT && echo ligada || echo desligada ;;
	*) echo "Uso: $0 on|off|status"; exit 2 ;;
esac
