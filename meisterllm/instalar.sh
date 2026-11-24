#!/usr/bin/env bash
# Prepara uma rig nova: motor llama.cpp (binários oficiais, CUDA 12.8), bibliotecas CUDA e as bibliotecas
# de sistema do Ubuntu 24.04 de que ele precisa. Tudo fica em RIG_HOME; o HiveOS não é alterado.
set -e
RIG_HOME=${RIG_HOME:-/home/user/meister-rig}
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD=b12651
BASE=https://github.com/ggml-org/llama.cpp/releases/download/$BUILD
mkdir -p "$RIG_HOME/dl" "$RIG_HOME/llama" "$RIG_HOME/modelos"
# O motor e as bibliotecas ocupam ~6 GB; o modelo vem depois e costuma ser o maior (o 27B são 17 GB).
FREE=$(df -BG --output=avail "$RIG_HOME" | tail -1 | tr -dc 0-9)
if [[ ! -x $RIG_HOME/llama/llama-$BUILD/llama-server && ${FREE:-0} -lt 8 ]]; then echo "Disco quase cheio: $FREE GB livres em $RIG_HOME, são precisos pelo menos 8 só para o motor."; exit 1; fi
command -v nvidia-smi > /dev/null || { echo "Não encontro o nvidia-smi: esta rig precisa de placas NVIDIA com o driver do HiveOS."; exit 1; }
cd "$RIG_HOME/dl"
cat > sums <<EOF
cb099350c6a025e67eefaa13f2b67b2dcce53e38a0da22476e070d166ef367ea  llama-$BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz
485df8f0b0ab30c15807d25fd23a821aeaef62a3947722c683429491a5e0d027  cudart-llama-$BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz
EOF
if [[ ! -x $RIG_HOME/llama/llama-$BUILD/llama-server ]]; then
	for f in llama-$BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz cudart-llama-$BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz; do wget -q -c "$BASE/$f"; done
	sha256sum -c sums
	for f in *.tar.gz; do tar -xzf "$f" -C "$RIG_HOME/llama"; done
fi
[[ -e $RIG_HOME/noble/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 ]] || RIG_HOME="$RIG_HOME" python3 "$HERE/glibc_noble.py"
# Memória de reserva de 8 GB (o minerador liga-a ao arrancar).
if [[ ! -f $RIG_HOME/swapfile ]]; then fallocate -l 8G "$RIG_HOME/swapfile" && chmod 600 "$RIG_HOME/swapfile" && mkswap "$RIG_HOME/swapfile" > /dev/null; fi
# Transcrição de voz (opcional: INSTALAR_WHISPER=1). faster-whisper e cuDNN do PyPI numa pasta à parte; modelo preso a uma revisão.
if [[ $INSTALAR_WHISPER == 1 && ! -d $RIG_HOME/whisper/site/faster_whisper ]]; then
	W=$RIG_HOME/whisper; M=$W/modelo/large-v3-turbo; mkdir -p "$W/pip" "$W/site" "$M"
	wget -q -O "$W/get-pip.py" https://bootstrap.pypa.io/get-pip.py && python3 "$W/get-pip.py" --quiet --no-warn-script-location --target "$W/pip"
	PYTHONPATH=$W/pip python3 -m pip install --quiet --no-warn-script-location --target "$W/site" 'faster-whisper==1.2.1' 'nvidia-cudnn-cu12>=9,<10'
	R=https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/resolve/4df90f75321148c3a29a9e2351b7ddf8f5b115a8
	for f in config.json preprocessor_config.json tokenizer.json vocabulary.json model.bin; do wget -q -c -O "$M/$f" "$R/$f"; done
	echo 'e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da  model.bin' | (cd "$M" && sha256sum -c)
fi
install -m 755 "$HERE/llama.sh" "$RIG_HOME/llama.sh"
"$RIG_HOME/llama.sh" llama-server --list-devices
