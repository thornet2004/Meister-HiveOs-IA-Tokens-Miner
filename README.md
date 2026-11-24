# Pôr o «minerador de tokens» numa rig nova (HiveOS)

Transforma uma rig de mineração num servidor de modelos de IA para a **[MeisterCode](https://github.com/thornet2004/Meister-Code)**, uma aplicação de secretária para Windows com conversa, geração de imagem, vídeo e áudio e agente de programação. Na app, a rig liga-se em Configurações → «Onde correm os modelos» → «Provedor local». Também se pode usar sem a app: a rig serve uma página de conversa em `/chat` e uma API compatível com a da OpenAI.

Serve para uma rig com HiveOS e placas NVIDIA que nunca teve este sistema. Depois do pacote estar na rig, **tudo o resto é automático** no primeiro arranque: instala o motor, descarrega e confere o modelo, reparte-o pelas placas, cria a chave de dono e liga o porteiro.

> Estado: a versão 0.8 (instalação e plano automáticos) foi ensaiada no PC (contas do plano, sintaxe, pacote). **Ainda não correu numa rig limpa.** A primeira instalação a sério é o ensaio que falta.

## O que a rig precisa
- HiveOS com o driver NVIDIA (o `nvidia-smi` tem de responder).
- Disco: ~6 GB para o motor, mais o modelo (o Qwen3.8-27B são 17 GB). Com Whisper, mais ~4 GB.
- Placas: o modelo tem de caber na soma delas. O 27B precisa de três placas de 8 GB; com duas não arranca e o registo diz porquê.

## 1. Fazer o pacote (só quem mantém o projeto; quem instala salta para o passo 2)
```
python empacotar.py
```
Fica em `pacote/meisterllm-0.8.tar.gz` (não vai para o git). Não leva chaves, modelos nem dados de nenhuma rede.

## 2. Pô-lo na rig — um de dois caminhos
**A. Pelo painel do Hive («Installation URL») — o mais simples.** O Hive descarrega e desempacota sozinho; não é preciso entrar na rig. O endereço é o do ficheiro `meisterllm-0.8.tar.gz` no repositório público:
```
https://github.com/thornet2004/Meister-HiveOs-IA-Tokens-Miner/raw/main/meisterllm-0.8.tar.gz
```
(um repositório privado não serve: a rig não consegue descarregar sem sessão iniciada).

**B. À mão, uma vez.** Copia o ficheiro para a rig e desempacota:
```
scp pacote/meisterllm-0.8.tar.gz user@<endereço da rig>:/tmp/
ssh user@<endereço da rig> "sudo mkdir -p /hive/miners/custom && sudo tar -xzf /tmp/meisterllm-0.8.tar.gz -C /hive/miners/custom/"
```

## 3. Flight sheet no painel do Hive
Cria uma carteira qualquer para a moeda (o endereço não é usado) e um flight sheet com minerador **Custom**:

| Campo | Valor |
|---|---|
| Miner name | `meisterllm` |
| Installation URL | o endereço do pacote (caminho A) ou vazio (caminho B) |
| Hash algorithm | vazio |
| Wallet and worker template | `%WAL%` |
| Pool URL | endereço do modelo `.gguf` — ver abaixo |
| Pass | SHA-256 do modelo (64 caracteres); vazio = não confere |
| Extra config arguments | ver abaixo |

**Modelo usado na rig de origem** (Apache 2.0, 16,6 GB):
- Pool URL: `https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-Q4_K_M.gguf`
- Pass: `1bce504dfecd84153666928a41d8a66706f0158d3a317bc5196b72819d7dc529`

**Extra config arguments para começar** (uma por linha):
```
FIREWALL=0
EXTRA=--reasoning-effort low
```
- `FIREWALL=0`: a firewall isola a rig do resto da rede de casa; liga-se (`FIREWALL=1`) depois de se perceber o que faz (`firewall.sh`).
- `EXTRA=--reasoning-effort low`: o 27B pensa menos antes de responder (sem isto demora bastante mais).
- Voz: `WHISPER_GPU=<índice da placa>` instala e liga a transcrição (precisa de ~1,2 GB livres nessa placa — só numa placa que o plano tenha deixado sem uso).

## 4. O primeiro arranque
Aplica o flight sheet e acompanha o registo do minerador no painel. Por ordem:
1. «A preparar a rig…» — motor e bibliotecas (alguns minutos).
2. «A descarregar o modelo…» — o mais demorado (17 GB).
3. «Plano automático: «nome» nas placas 0,1,2, contexto 32768.» — uma linha por servidor.
4. «… pronto na porta 8081» e «Porteiro pronto na porta 8080».

**Chave de dono:** nasce na rig e não aparece no painel. Lê-se na consola da rig:
```
cat /home/user/meister-rig/api_key
```
Com ela entra-se em `http://<endereço da rig>:8080/admin` (criar chaves para outras pessoas) e em `http://<endereço da rig>:8080/chat`. Na app [MeisterCode](https://github.com/thornet2004/Meister-Code): Configurações → «Provedor local» → endereço `http://<endereço da rig>:8080` e a chave.

## Como o plano automático faz as contas
Vai juntando placas pela ordem até a memória chegar para o modelo, com folga: 1,2 GB por placa e 2 GB por servidor para um contexto de 16 384; havendo mais 3 GB, o contexto sobe para 32 768. As placas que sobram formam outro servidor com o mesmo modelo, se chegarem. É prudente de propósito; quem quiser espremer a rig (64k de contexto, dois modelos diferentes) escreve linhas `SERVERn=` à mão, como em `flight-sheet-extra.txt`.

## Se correr mal
- «O modelo não cabe nas placas desta rig»: escolher um modelo mais pequeno no Pool URL.
- «SHA-256 não bate certo»: a descarga veio estragada ou o Pass está errado; apagar o ficheiro em `/home/user/meister-rig/modelos/` e voltar a arrancar.
- Um servidor «não arrancou»: o motivo está em `/hive/miners/custom/meisterllm/run/s0.log` (quase sempre falta de memória numa placa — baixar o contexto com `CTX=16384`).
- O acesso de fora de casa (domínio, HTTPS) não é feito por este pacote: é com o dono da rede.
