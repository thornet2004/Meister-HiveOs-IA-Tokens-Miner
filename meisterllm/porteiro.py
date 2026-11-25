#!/usr/bin/env python3
# Porteiro da rig: uma porta única à frente dos servidores (um modelo por porta) e do Whisper.
# É por aqui que se entra de fora (atrás de um proxy HTTPS) e é aqui que vivem as chaves dos amigos.
#   GET    /health                    -> {"status": "ok"}                                  (sem chave)
#   GET    /v1/models                 -> os modelos que esta chave pode usar, com o contexto de cada um
#   POST   /v1/chat/completions       -> vai para o servidor do modelo pedido em «model»
#   POST   /v1/completions /completion /infill -> idem (completação/FIM: o autocomplete em linha do editor)
#   POST   /transcrever?language=pt   -> vai para o Whisper
#   POST   /extrair?tipo=imagem|pdf   -> texto de uma imagem (OCR) ou de um PDF
#   GET    /mobile                    -> página de conversa (pede a chave; serve para o telemóvel e para amigos sem a app)
#   GET    /admin                     -> página de administração (só de dentro da rede; pede a chave de dono)
#   GET    /admin/estado              -> amigos, modelos e uso           (só o dono, e só de dentro da rede)
#   POST   /admin/amigos              -> {"nome", "modelos"} cria um amigo; a chave dele aparece só nesta resposta
#   POST   /admin/amigos/<nome>       -> {"ativo": bool, "modelos": [...], "nova_chave": true} altera
#   DELETE /admin/amigos/<nome>       -> apaga
# Tudo o resto não existe. O dono é quem tem a chave da rig (run/key); dos amigos guarda-se só o SHA-256 da chave.
# Não se regista o que se pergunta nem o que se responde.
#
# MODO TROCA (opcional): se houver SLOTS no ambiente, o porteiro passa a dono do ciclo de vida dos modelos.
# Em vez de servidores sempre ligados, há um CATÁLOGO (run/catalogo) de modelos disponíveis e um número fixo de
# «vagas» (SLOTS, cada uma um grupo de placas). Um modelo é carregado quando é pedido e ocupa uma vaga; se não houver
# vaga livre, descarrega-se o modelo parado há mais tempo (nunca um com pedidos a decorrer). Os modelos «quentes»
# (QUENTES) são pré-carregados e repostos nas vagas livres. Só os modelos carregados pesam na VRAM.
# Uso: porteiro.py <porta> [endereço] [pasta run]
# Ambiente: RIG_HOME, GATE_PROXY, GATE_FRIEND_PARALLEL, GATE_BACKEND;
#   e, para o modo troca: SLOTS, QUENTES, VAGA_FIXA, KEEPALIVE, MODEL_PORT, CTX, PARALLEL, CACHE_RAM, CHECKPOINTS,
#   EXTRA, LLAMA_SH (ou LLAMA_BASE, lista JSON, para ensaios).
import atexit
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

PORT = int(sys.argv[1])
HOST = sys.argv[2] if len(sys.argv) > 2 else '0.0.0.0'
RUN = sys.argv[3] if len(sys.argv) > 3 else 'run'
RIG_HOME = os.environ.get('RIG_HOME', '/home/user/meister-rig')
FRIENDS = os.path.join(RIG_HOME, 'amigos.json')
PROXIES = set(os.environ.get('GATE_PROXY', '').replace(',', ' ').split())  # endereços do proxy HTTPS (ex.: 192.168.1.10)
FRIEND_PARALLEL = int(os.environ.get('GATE_FRIEND_PARALLEL') or 2)        # conversas em simultâneo por amigo
BACKEND = os.environ.get('GATE_BACKEND', '127.0.0.1')
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, 'porteiro_admin.html')
CHAT = os.path.join(HERE, 'porteiro_chat.html')

# Modo troca (ligado quando há SLOTS). Cada vaga é um grupo de placas; um modelo por vaga.
SLOTS = [grupo for grupo in os.environ.get('SLOTS', '').split() if grupo]
SWAP = bool(SLOTS)
PINNED = [n for n in os.environ.get('QUENTES', '').split() if n]          # modelos a manter quentes
KEEPALIVE = int(os.environ.get('KEEPALIVE') or 600)                        # segundos parado antes de descarregar um não-fixo
MODEL_PORT = int(os.environ.get('MODEL_PORT') or (PORT + 1))              # a vaga k responde em MODEL_PORT + k
CTX_DEFAULT = int(os.environ.get('CTX') or 32768)
PARALLEL = os.environ.get('PARALLEL') or '1'
CACHE_RAM = os.environ.get('CACHE_RAM') or '512'
CHECKPOINTS = os.environ.get('CHECKPOINTS') or '4'
EXTRA = os.environ.get('EXTRA', '')
LLAMA_SH = os.environ.get('LLAMA_SH', os.path.join(RIG_HOME, 'llama.sh'))
LLAMA_BASE = json.loads(os.environ['LLAMA_BASE']) if os.environ.get('LLAMA_BASE') else [LLAMA_SH, 'llama-server']
LOAD_WAIT = int(os.environ.get('LOAD_WAIT') or 180)                        # segundos à espera que um modelo fique pronto
# Vaga fixa: modelos que só podem carregar numa vaga concreta. Serve para um modelo de contexto grande que não cabe
# na vaga cuja placa é partilhada com o Whisper. Formato: "nome:índice" separados por espaços (ex.: "qwen3-coder-30b:1").
FIXED_SLOT = {}
for _item in os.environ.get('VAGA_FIXA', '').split():
    _nome, _, _idx = _item.partition(':')
    if _nome and _idx.isdigit() and 0 <= int(_idx) < len(SLOTS):
        FIXED_SLOT[_nome] = int(_idx)

# O que a página de conversa precisa para se instalar como app no telemóvel (tudo público, sem segredos).
STATIC = {'/manifest.webmanifest': ('porteiro_manifest.json', 'application/manifest+json'),
          '/sw.js': ('porteiro_sw.js', 'text/javascript; charset=utf-8'),
          '/icon-192.png': ('porteiro_icon_192.png', 'image/png'),
          '/icon-512.png': ('porteiro_icon_512.png', 'image/png'),
          '/apple-touch-icon.png': ('porteiro_icon_192.png', 'image/png')}
MAX_CHAT = 16 * 1024 * 1024
MAX_AUDIO = 40 * 1024 * 1024
MAX_FILE = 40 * 1024 * 1024
OCR_LANGS = os.environ.get('OCR_LANGS', 'por+eng')  # línguas do OCR (tesseract); «por» = português
PC_IP = os.environ.get('PC_IP', '')  # IP do PC do dono, para pré-preencher o «Acesso remoto ao PC» na página mobile (fica fora do código público)
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$')
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$')
FAILS, FAIL_WINDOW = 10, 300  # chaves erradas toleradas por endereço, em quantos segundos

lock = threading.Lock()
use = {}      # nome -> pedidos de hoje, última vez, conversas a decorrer
fails = {}    # endereço -> momentos das chaves erradas
contexts = {}  # porta -> (momento, contexto)

# Estado do modo troca.
load_lock = threading.RLock()     # protege «loaded» e «slot_of»; carregar/descarregar é serializado por este trinco
loaded = {}                       # nome -> dict(port, slot, proc, last, inflight, ctx)
slot_of = [None] * len(SLOTS)     # índice da vaga -> nome do modelo lá carregado, ou None


def read(name):
    try:
        with open(os.path.join(RUN, name), encoding='utf-8') as handle:
            return handle.read().strip()
    except OSError:
        return ''


def servers():
    """Os servidores de texto a correr: [(porta, nome)], pela ordem do flight sheet (modo antigo)."""
    found = []
    for line in read('servers').splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            found.append((int(parts[0]), parts[3]))
    return found


def whisper_port():
    parts = read('whisper').split()
    return int(parts[0]) if parts and parts[0].isdigit() else 0


def extract_text(data, is_pdf):
    """Texto de um ficheiro: PDF por «pdftotext», imagem por OCR («tesseract»). Devolve (texto, erro)."""
    try:
        with tempfile.NamedTemporaryFile(suffix='.pdf' if is_pdf else '.img', delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
    except OSError:
        return '', 'não consegui guardar o ficheiro'
    try:
        cmd = ['pdftotext', '-q', path, '-'] if is_pdf else ['tesseract', path, 'stdout', '-l', OCR_LANGS]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=120)
        except FileNotFoundError:
            return '', 'a ferramenta de extração não está instalada na rig'
        except (OSError, subprocess.TimeoutExpired):
            return '', 'não consegui ler o ficheiro'
        if done.returncode != 0:
            return '', 'não consegui ler o ficheiro'
        return done.stdout.decode('utf-8', 'replace').strip(), ''
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---- Catálogo e gestor de modelos (modo troca) ------------------------------------------------

def catalog():
    """Modelos disponíveis, do ficheiro run/catalogo: nome -> dict(file, ctx, args). Ordem preservada."""
    items = {}
    for line in read('catalogo').splitlines():
        parts = line.split('|')
        if len(parts) >= 2 and parts[0].strip():
            name = parts[0].strip()
            ctx = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else CTX_DEFAULT
            items[name] = dict(file=parts[1].strip(), ctx=ctx, args=parts[3] if len(parts) > 3 else '')
    return items


def write_servers():
    """Mantém run/servers a espelhar os modelos carregados, para o h-stats.sh medir tokens/s por placa."""
    lines = ['%d %d %s %s' % (info['port'], info['proc'].pid if info.get('proc') else 0, SLOTS[info['slot']], name)
             for name, info in loaded.items()]
    try:
        with open(os.path.join(RUN, 'servers'), 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + ('\n' if lines else ''))
    except OSError:
        pass


def _healthy(port):
    try:
        connection = http.client.HTTPConnection(BACKEND, port, timeout=2)
        connection.request('GET', '/health')
        answer = connection.getresponse()
        ok = answer.status == 200 and b'ok' in answer.read()
        connection.close()
        return ok
    except (OSError, http.client.HTTPException):
        return False


def _free_port():
    """Uma porta livre para um servidor de modelo (a vaga só manda nas placas; a porta interna é indiferente)."""
    with socket.socket() as sock:
        sock.bind((BACKEND, 0))
        return sock.getsockname()[1]


def _launch(name, slot):
    """Arranca o modelo «name» na vaga «slot» e espera que fique pronto. Devolve a porta, ou None se falhar."""
    meta = catalog().get(name)
    if not meta:
        return None
    gpus, port = SLOTS[slot], _free_port()
    argv = list(LLAMA_BASE) + ['-m', meta['file'], '--alias', name, '-ngl', '99', '-c', str(meta['ctx']),
                               '-np', PARALLEL, '--host', BACKEND, '--port', str(port), '--metrics',
                               '--cache-ram', CACHE_RAM, '--ctx-checkpoints', CHECKPOINTS]
    key = read('key')
    if key:
        argv += ['--api-key', key]
    argv += shlex.split(EXTRA) + shlex.split(meta['args'])
    try:
        log = open(os.path.join(RUN, 's%d.log' % slot), 'ab')
    except OSError:
        log = subprocess.DEVNULL
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=gpus)
    try:
        proc = subprocess.Popen(argv, stdout=log, stderr=log, env=env)
    except OSError:
        return None
    for _ in range(LOAD_WAIT):
        if _healthy(port):
            loaded[name] = dict(port=port, slot=slot, proc=proc, last=time.time(), inflight=0, ctx=meta['ctx'])
            slot_of[slot] = name
            write_servers()
            return port
        if proc.poll() is not None:
            break
        time.sleep(1)
    try:
        proc.kill()
    except OSError:
        pass
    return None


def _stop(name):
    """Descarrega um modelo (liberta a vaga e a VRAM)."""
    info = loaded.pop(name, None)
    if not info:
        return
    slot_of[info['slot']] = None
    proc = info.get('proc')
    if proc:
        try:
            proc.terminate()
            proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass
    write_servers()


def _free_slot():
    return next((i for i, n in enumerate(slot_of) if n is None), -1)


def _evict():
    """Liberta uma vaga descarregando o modelo parado há mais tempo e sem pedidos. Prefere não-fixos. -1 se nenhum livre."""
    livres = [(info['last'], name) for name, info in loaded.items() if info['inflight'] == 0]
    if not livres:
        return -1
    nao_fixos = [c for c in livres if c[1] not in PINNED]
    (nao_fixos or livres).sort()
    alvo = (nao_fixos or livres)[0][1]
    slot = loaded[alvo]['slot']
    _stop(alvo)
    return slot


def _load(name):
    """Garante o modelo carregado (carrega-o, abrindo vaga se preciso). Devolve a porta, ou None. Sob load_lock."""
    if name in loaded:
        return loaded[name]['port']
    if name not in catalog():
        return None
    want = FIXED_SLOT.get(name)
    if want is not None:                       # modelo preso a uma vaga: carrega só lá
        ocupante = slot_of[want]
        if ocupante and ocupante != name:
            if loaded[ocupante]['inflight'] > 0:
                return None                    # a vaga está ocupada por um modelo em uso; não dá para carregar agora
            _stop(ocupante)
        slot = want
    else:
        slot = _free_slot()
        if slot < 0:
            slot = _evict()
        if slot < 0:
            return None
    return _launch(name, slot)


def ensure(name):
    """Garante o modelo pronto e marca um pedido a decorrer (protege-o de ser descarregado). Porta, ou None."""
    with load_lock:
        port = _load(name)
        if port is None:
            return None
        loaded[name]['inflight'] += 1
        loaded[name]['last'] = time.time()
        return port


def done(name):
    with load_lock:
        if name in loaded:
            loaded[name]['inflight'] = max(0, loaded[name]['inflight'] - 1)
            loaded[name]['last'] = time.time()


def model_names():
    """Nomes dos modelos disponíveis, por ordem: o catálogo (modo troca) ou os servidores a correr (modo antigo)."""
    return list(catalog().keys()) if SWAP else [name for _, name in servers()]


def info(name):
    """(contexto, lê imagens?) de um modelo: perguntado ao servidor se estiver carregado, senão o do catálogo."""
    if SWAP:
        with load_lock:
            port = loaded[name]['port'] if name in loaded else 0
        if port:
            return facts(port)
        meta = catalog().get(name) or {}
        return meta.get('ctx', 0), False
    port = next((p for p, n in servers() if n == name), 0)
    return facts(port) if port else (0, False)


def _keepalive():
    """De tempos a tempos: descarrega modelos não-fixos parados há muito e repõe os fixos nas vagas livres."""
    while True:
        time.sleep(min(30, max(5, KEEPALIVE // 4)))
        with load_lock:
            agora = time.time()
            for name in list(loaded):
                if name not in PINNED and loaded[name]['inflight'] == 0 and agora - loaded[name]['last'] > KEEPALIVE:
                    _stop(name)
            for name in PINNED:  # repõe os quentes nas vagas que sobrarem
                if _free_slot() < 0:
                    break
                if name not in loaded and name in catalog():
                    _load(name)


def _preload():
    with load_lock:
        for name in PINNED:
            if _free_slot() < 0:
                break
            if name in catalog():
                _load(name)


def _shutdown(*_):
    with load_lock:
        for name in list(loaded):
            _stop(name)


def load():
    try:
        with open(FRIENDS, encoding='utf-8') as handle:
            data = json.load(handle)
        return [f for f in data.get('amigos', []) if isinstance(f, dict) and f.get('nome') and f.get('hash')]
    except (OSError, ValueError, AttributeError):
        return []


def save(friends):
    temporary = FRIENDS + '.novo'
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as handle:
        json.dump(dict(amigos=friends), handle, ensure_ascii=False, indent=1)
    os.replace(temporary, FRIENDS)


def digest(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def who(key):
    """De quem é a chave: o dono, um amigo ativo ou ninguém."""
    if not key:
        return None
    owner = read('key')
    if owner and hmac.compare_digest(key.encode(), owner.encode()):
        return dict(dono=True, nome='dono', modelos=None)
    mine = digest(key)
    for friend in load():
        if friend.get('ativo', True) and hmac.compare_digest(mine, str(friend['hash'])):
            return dict(dono=False, nome=friend['nome'], modelos=list(friend.get('modelos') or []))
    return None


def allowed(user):
    """(modo antigo) servidores a correr ao alcance da chave: [(porta, nome)]."""
    return [(port, name) for port, name in servers() if user['dono'] or name in user['modelos']]


def allowed_names(user):
    """Nomes de modelos ao alcance da chave (os dois modos)."""
    return [n for n in model_names() if user['dono'] or n in user['modelos']]


def facts(port):
    """(contexto, lê imagens?) do modelo nessa porta — perguntado ao servidor, guardado um minuto."""
    known = contexts.get(port)
    if known and time.time() - known[0] < 60:
        return known[1]
    value, vision = 0, False
    try:
        connection = http.client.HTTPConnection(BACKEND, port, timeout=5)
        key = read('key')
        connection.request('GET', '/props', headers={'Authorization': 'Bearer ' + key} if key else {})
        answer = connection.getresponse()
        if answer.status == 200:
            props = json.loads(answer.read())
            value = int((props.get('default_generation_settings') or {}).get('n_ctx') or 0)
            vision = bool((props.get('modalities') or {}).get('vision'))
        connection.close()
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        pass
    if value:
        contexts[port] = (time.time(), (value, vision))
    return value, vision


def counted(name):
    today = time.strftime('%Y-%m-%d')
    entry = use.setdefault(name, dict(dia=today, pedidos=0, ultimo=0, a_decorrer=0))
    if entry['dia'] != today:
        entry.update(dia=today, pedidos=0)
    return entry


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    timeout = 120  # uma ligação parada não fica presa para sempre
    server_version = 'meister'
    sys_version = ''

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, message):
        self.close_connection = True  # o corpo do pedido pode não ter sido lido
        self.reply(status, dict(error=dict(message=message, code=status)))

    def origin(self):
        """(endereço de quem pede, veio de fora?). «De fora» = passou por um proxy."""
        peer = self.client_address[0]
        forwarded = (self.headers.get('X-Real-IP') or (self.headers.get('X-Forwarded-For') or '').split(',')[-1]).strip()
        if peer in PROXIES:
            return (forwarded or peer)[:60], True
        return peer, bool(forwarded or self.headers.get('Forwarded'))

    def user(self):
        """Quem pede, ou None (já respondido)."""
        address, outside = self.origin()
        now = time.time()
        header = self.headers.get('Authorization', '')
        found = who(header[7:].strip() if header.startswith('Bearer ') else '')
        if not found:
            # Uma chave certa passa sempre: numa casa todos os aparelhos saem pelo mesmo endereço, e as tentativas
            # erradas de um não podem deixar os outros à porta. O bloqueio só trava mais chaves erradas.
            with lock:
                recent = [t for t in fails.get(address, []) if now - t < FAIL_WINDOW]
                fails[address] = recent
                if len(recent) >= FAILS:
                    self.error(429, 'Demasiadas chaves erradas deste endereço. Tenta daqui a uns minutos.')
                    return None
            with lock:
                fails.setdefault(address, []).append(now)
                if len(fails) > 5000:
                    fails.clear()
            self.error(401, 'Chave em falta ou errada.')
            return None
        found['fora'] = outside
        return found

    def owner(self):
        found = self.user()
        if not found:
            return None
        if not found['dono'] or found['fora']:
            self.error(403, 'Só o dono, e só de dentro da rede dele.')
            return None
        return found

    def body(self, limit):
        size = self.headers.get('Content-Length')
        if size is None or not size.isdigit():
            self.error(411, 'Falta o tamanho do pedido.')
            return None
        if int(size) > limit:
            self.error(413, 'Pedido demasiado grande.')
            return None
        return self.rfile.read(int(size))

    def forward(self, port, path, body, kind):
        """Entrega o pedido ao servidor e devolve a resposta à medida que ela chega."""
        key = read('key')
        headers = {'Content-Type': kind, 'Content-Length': str(len(body))}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        connection = http.client.HTTPConnection(BACKEND, port, timeout=900)
        try:
            connection.request('POST', path, body, headers)
            answer = connection.getresponse()
        except (OSError, http.client.HTTPException):
            connection.close()
            return self.error(502, 'O servidor do modelo não respondeu.')
        try:
            length = answer.getheader('Content-Length')
            self.send_response(answer.status)
            self.send_header('Content-Type', answer.getheader('Content-Type') or 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Accel-Buffering', 'no')  # o nginx entrega logo cada bocado em vez de juntar tudo
            self.send_header('Content-Length', length) if length is not None else self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            while True:
                piece = answer.read1(65536)
                if not piece:
                    break
                self.wfile.write(piece if length is not None else b'%x\r\n%s\r\n' % (len(piece), piece))
                self.wfile.flush()
            if length is None:
                self.wfile.write(b'0\r\n\r\n')
        except (OSError, http.client.HTTPException):
            self.close_connection = True  # quem pediu desligou: fechar a ligação ao servidor fá-lo parar de gerar
        finally:
            connection.close()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            return self.reply(200, dict(status='ok'))
        if path == '/v1/models':
            user = self.user()
            if not user:
                return
            # «vision»: sem o projetor de imagem carregado, o servidor lê uma imagem anexada como texto e o pedido rebenta o contexto
            if SWAP:
                data = [dict(id=name, object='model', owned_by='meister-rig', context=info(name)[0], vision=info(name)[1])
                        for name in allowed_names(user)]
            else:
                data = [dict(id=name, object='model', owned_by='meister-rig', context=facts(port)[0], vision=facts(port)[1])
                        for port, name in allowed(user)]
            return self.reply(200, dict(object='list', porteiro=1, papel='dono' if user['dono'] else 'amigo',
                                        admin=bool(user['dono'] and not user['fora']), whisper=bool(whisper_port()), data=data))
        if path in ('/', '/chat', '/chat/'):  # a página mudou-se para /mobile; a raiz e os links antigos reencaminham para lá
            self.send_response(302)
            self.send_header('Location', '/mobile')
            self.send_header('Content-Length', '0')
            return self.end_headers()
        if path in STATIC:
            name, kind = STATIC[path]
            try:
                with open(os.path.join(HERE, name), 'rb') as handle:
                    data = handle.read()
            except OSError:
                return self.error(404, 'Não existe.')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache' if path == '/sw.js' else 'public, max-age=86400')
            self.end_headers()
            return self.wfile.write(data)
        if path in ('/admin', '/admin/', '/mobile', '/mobile/'):
            admin = path.startswith('/admin')
            if admin and self.origin()[1]:
                return self.error(404, 'Não existe.')  # de fora, a página de administração nem existe
            try:
                with open(PAGE if admin else CHAT, 'rb') as handle:
                    page = handle.read()
            except OSError:
                return self.error(404, 'Não existe.')
            if not admin:  # o IP do PC do dono entra aqui (variável PC_IP), não fica escrito no código
                page = page.replace(b'__PC_IP__', PC_IP.encode('utf-8'))
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            return self.wfile.write(page)
        if path == '/admin/estado':
            if not self.owner():
                return
            with lock:
                friends = [dict(nome=f['nome'], modelos=f.get('modelos') or [], ativo=bool(f.get('ativo', True)), criado=f.get('criado', ''),
                                pedidos_hoje=counted(f['nome'])['pedidos'], ultimo=counted(f['nome'])['ultimo'],
                                a_decorrer=counted(f['nome'])['a_decorrer']) for f in load()]
            return self.reply(200, dict(amigos=friends, modelos=model_names(), whisper=bool(whisper_port())))
        self.error(404, 'Não existe.')

    def do_POST(self):
        url = urlparse(self.path)
        if url.path in ('/v1/chat/completions', '/v1/completions', '/completion', '/infill'):
            return self.proxy(url.path)
        if url.path == '/transcrever':
            user = self.user()
            if not user:
                return
            port = whisper_port()
            if not port:
                return self.error(503, 'Esta rig não tem transcrição de voz ligada.')
            audio = self.body(MAX_AUDIO)
            if audio is None:
                return
            language = re.sub(r'[^a-z]', '', (url.query.partition('language=')[2] or 'pt').split('&')[0])[:5] or 'pt'
            return self.forward(port, '/transcrever?language=' + language, audio, self.headers.get('Content-Type') or 'audio/wav')
        if url.path == '/extrair':
            if not self.user():
                return
            data = self.body(MAX_FILE)
            if data is None:
                return
            tipo = url.query.partition('tipo=')[2].split('&')[0]
            is_pdf = tipo == 'pdf' or (not tipo and data[:5] == b'%PDF-')
            texto, erro = extract_text(data, is_pdf)
            if erro:
                return self.error(422, 'Não consegui extrair o texto: ' + erro)
            return self.reply(200, dict(texto=texto, caracteres=len(texto)))
        if url.path == '/admin/amigos' or url.path.startswith('/admin/amigos/'):
            return self.friends(unquote(url.path[len('/admin/amigos/'):]))
        self.error(404, 'Não existe.')

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith('/admin/amigos/'):
            return self.friends(unquote(path[len('/admin/amigos/'):]), remove=True)
        self.error(404, 'Não existe.')

    def proxy(self, path):
        """Encaminha um pedido de geração (chat, completações ou infill) para o servidor do modelo pedido em «model».
        O mesmo caminho vale para todos: o autocomplete do editor usa /v1/completions ou /infill, o chat usa
        /v1/chat/completions. Sem «model», vai para o primeiro modelo ao alcance da chave."""
        user = self.user()
        if not user:
            return
        raw = self.body(MAX_CHAT)
        if raw is None:
            return
        try:
            wanted = json.loads(raw).get('model')
        except (ValueError, AttributeError):
            return self.error(400, 'O pedido não é JSON válido.')
        names = allowed_names(user)
        name = wanted if wanted in names else (names[0] if names and not wanted else None)
        if not name:
            return self.error(404, 'Esse modelo não existe ou não está ao teu alcance.')
        with lock:
            entry = counted(user['nome'])
            if not user['dono'] and entry['a_decorrer'] >= FRIEND_PARALLEL:
                return self.error(429, 'Já tens pedidos a decorrer; espera que acabem.')
            entry['a_decorrer'] += 1
            entry['pedidos'] += 1
            entry['ultimo'] = int(time.time())
        try:
            if SWAP:
                port = ensure(name)  # carrega o modelo se preciso (pode demorar); marca um pedido a decorrer
                if port is None:
                    return self.error(503, 'A rig está a carregar outro modelo ou sem vaga livre; tenta daqui a pouco.')
                try:
                    self.forward(port, path, raw, 'application/json')
                finally:
                    done(name)
            else:
                port = next((p for p, n in servers() if n == name), 0)
                if not port:
                    return self.error(404, 'Esse modelo não existe ou não está ao teu alcance.')
                self.forward(port, path, raw, 'application/json')
        finally:
            with lock:
                entry['a_decorrer'] -= 1

    def friends(self, name, remove=False):
        if not self.owner():
            return
        data = {}
        if not remove:
            raw = self.body(65536)
            if raw is None:
                return
            try:
                data = json.loads(raw or b'{}')
                assert isinstance(data, dict)
            except (ValueError, AssertionError):
                return self.error(400, 'O pedido não é JSON válido.')
        creating = not name and not remove
        name = str(data.get('nome') or '').strip() if creating else name
        if not NAME.match(name) or name.lower() == 'dono':
            return self.error(400, 'Nome inválido: letras, números, espaço, ponto, hífen e sublinhado, até 40.')
        models = data.get('modelos')
        if models is not None and not (isinstance(models, list) and all(isinstance(m, str) and MODEL.match(m) for m in models)):
            return self.error(400, 'Lista de modelos inválida.')
        with lock:
            friends = load()
            found = next((f for f in friends if f['nome'].lower() == name.lower()), None)
            if creating:
                if found:
                    return self.error(409, 'Já existe um amigo com esse nome.')
                if models is None:  # sem escolha: só o primeiro modelo da rig
                    models = model_names()[:1]
                key = 'mr-' + secrets.token_urlsafe(32)
                friends.append(dict(nome=name, hash=digest(key), modelos=models, ativo=True, criado=time.strftime('%Y-%m-%d')))
                save(friends)
                return self.reply(201, dict(nome=name, modelos=models, chave=key))
            if not found:
                return self.error(404, 'Não há nenhum amigo com esse nome.')
            if remove:
                friends.remove(found)
                use.pop(found['nome'], None)
            else:
                if models is not None:
                    found['modelos'] = models
                if isinstance(data.get('ativo'), bool):
                    found['ativo'] = data['ativo']
                if data.get('nova_chave') is True:  # a chave antiga deixa de servir
                    key = 'mr-' + secrets.token_urlsafe(32)
                    found['hash'] = digest(key)
                    save(friends)
                    return self.reply(200, dict(nome=found['nome'], chave=key))
            save(friends)
        self.reply(200, dict(ok=True))

    def log_message(self, *args):  # sem registo por pedido
        pass


if __name__ == '__main__':
    if SWAP:
        atexit.register(_shutdown)
        signal.signal(signal.SIGTERM, lambda *_: (_shutdown(), os._exit(0)))
        signal.signal(signal.SIGINT, lambda *_: (_shutdown(), os._exit(0)))
        threading.Thread(target=_preload, daemon=True).start()
        threading.Thread(target=_keepalive, daemon=True).start()
        print('Porteiro pronto na porta %d (modo troca: %d vaga(s), %d no catálogo, %d quente(s)%s).' % (
            PORT, len(SLOTS), len(catalog()), len(PINNED), '' if read('key') else '; SEM chave de dono: ninguém entra'), flush=True)
    else:
        print('Porteiro pronto na porta %d (%d modelo(s), %d amigo(s)%s).' % (
            PORT, len(servers()), len(load()), '' if read('key') else '; SEM chave de dono: ninguém entra'), flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
