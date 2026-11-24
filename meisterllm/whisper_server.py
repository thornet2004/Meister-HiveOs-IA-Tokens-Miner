#!/usr/bin/env python3
# Servidor de transcrição da rig (faster-whisper numa placa). Fala só o que a MeisterCode precisa:
#   GET  /health                      -> {"status": "ok"}
#   POST /transcrever?language=pt     -> corpo = ficheiro de áudio (WAV, WebM, ...); resposta {"text", "seconds", "audio_seconds"}
# Com chave (variável WHISPER_KEY), o POST exige «Authorization: Bearer <chave>».
# Uso: whisper_server.py <pasta do modelo> <porta> [endereço] [tipo de cálculo]
import hmac
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from faster_whisper import WhisperModel

MODEL_DIR, PORT = sys.argv[1], int(sys.argv[2])
HOST = sys.argv[3] if len(sys.argv) > 3 else '0.0.0.0'
COMPUTE = sys.argv[4] if len(sys.argv) > 4 else 'int8_float16'
KEY = os.environ.get('WHISPER_KEY', '')
MAX_BYTES = 40 * 1024 * 1024

model = WhisperModel(MODEL_DIR, device='cuda', compute_type=COMPUTE)
lock = threading.Lock()  # uma transcrição de cada vez: a placa é partilhada com um modelo de texto
done = dict(requests=0, audio_seconds=0.0)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            return self.reply(200, dict(status='ok'))
        if path == '/stats':
            return self.reply(200, done)
        self.reply(404, dict(error='Não existe.'))

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != '/transcrever':
            return self.reply(404, dict(error='Não existe.'))
        if KEY and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + KEY):
            return self.reply(401, dict(error='Chave em falta ou errada.'))
        size = int(self.headers.get('Content-Length') or 0)
        if size <= 0 or size > MAX_BYTES:
            return self.reply(413, dict(error='Áudio vazio ou demasiado grande.'))
        audio = self.rfile.read(size)
        language = (parse_qs(url.query).get('language') or ['pt'])[0][:5]
        language = None if language in ('', 'auto') else language  # None = o Whisper deteta a língua
        started = time.time()
        try:
            with lock:
                segments, info = model.transcribe(io.BytesIO(audio), language=language, beam_size=1, vad_filter=True,
                                                  condition_on_previous_text=False)
                text = ' '.join(segment.text.strip() for segment in segments).strip()
        except Exception as error:  # um áudio estragado não pode deitar o servidor abaixo
            return self.reply(400, dict(error='Não consegui transcrever: ' + str(error)[:200]))
        done['requests'] += 1
        done['audio_seconds'] += float(info.duration)
        self.reply(200, dict(text=text, seconds=round(time.time() - started, 3), audio_seconds=round(float(info.duration), 2),
                             language=info.language))

    def log_message(self, *args):  # sem registo por pedido: o que se diz não fica escrito em lado nenhum
        pass


print('Whisper pronto na porta %d (%s, %s).' % (PORT, os.path.basename(MODEL_DIR.rstrip('/')), COMPUTE), flush=True)
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
