#!/usr/bin/env python3
"""
AssemblyGuard - Pipeline de preparacao de dataset
=================================================
Resolve o problema de LABEL LEAKAGE do dataset original:

  Problema : o video vem de uma camera Hikvision com analytics + um overlay
             de texto "Etapa: X" gravado nos pixels. Um modelo treinado nesses
             frames aprende a LER o texto, nao a enxergar o processo.

  Solucao  : (1) usar o overlay como fonte de rotulo automatico via OCR
             (2) APAGAR o overlay da imagem antes de salvar o frame
             (3) fazer o split por SEGMENTO TEMPORAL, nao por frame aleatorio

Saida: dataset/{train,test}/<classe>/*.jpg  em 96x96, pronto pro Edge Impulse.
"""

import cv2
import numpy as np
import pytesseract
import re
import json
import os
import shutil
from collections import Counter, defaultdict
from difflib import SequenceMatcher

# ----------------------------------------------------------------------
# CONFIGURACAO
# ----------------------------------------------------------------------
VIDEO = '/mnt/user-data/uploads/WhatsApp_Video_2026-08-21_at_08_29_02.mp4'
OUT_DIR = 'dataset'
IMG_SIZE = 96
OCR_STRIDE = 15        # roda OCR a cada N frames (30fps -> 2 leituras/s)
SAVE_STRIDE = 5        # salva 1 frame a cada N (30fps -> 6 fps de dataset)
TEST_RATIO = 0.30      # fracao alvo de FRAMES no teste (alocada por segmento)

# Recorte da area util ANTES do resize (x1, y1, x2, y2).
# Sem isso, ao reduzir o frame inteiro 816x464 para 96x96 os detalhes que
# distinguem as etapas (ferro de solda, componentes na mao) viram poucos
# pixels. O recorte concentra a resolucao onde a acao acontece.
ROI = (130, 55, 735, 425)

# Etapas reais do processo, confirmadas pela distribuicao das leituras de OCR.
VOCAB = ['espera', 'preparo', 'montagem', 'solda',
         'empilhagem 1', 'empilhagem 2', 'fim']

# Regioes de overlay a apagar (x1, y1, x2, y2) no frame original 816x464.
# Medidas a partir do mapa de variancia temporal + inspecao visual.
OVERLAY_BOXES = [
    (0,   0,   285, 52),    # caixa "Etapa: X" (o rotulo - VAZAMENTO PRINCIPAL)
    (610, 0,   816, 175),   # painel Hikvision (Activity/Cycle/contadores)
    (480, 392, 816, 432),   # rodape com modelo da camera DS-2CD1027G2H-L
    (150, 128, 300, 164),   # texto "Home zone"
    (255, 112, 405, 145),   # texto "Work table"
    (550, 126, 715, 200),   # textos "Waiting" / "Donuts Stack" (direita)
    (145, 176, 300, 222),   # texto "Donuts Stack" (esquerda)
    (210, 278, 305, 308),   # texto "Glue gun"
    (278, 258, 405, 302),   # texto "Soldering iron" (tarja semitransparente)
]


# ----------------------------------------------------------------------
# 1. OCR DO ROTULO
# ----------------------------------------------------------------------
def ocr_etapa(frame):
    """Le o texto 'Etapa: X' do canto superior esquerdo.

    O texto muda de COR conforme a etapa (rosa, azul, amarelo, cinza...),
    entao testamos varios limiares sobre o canal maximo e ficamos com a
    leitura mais longa que contenha a palavra 'etapa'.
    """
    roi = frame[5:48, 15:265]
    roi = cv2.resize(roi, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    mx = roi.max(axis=2)

    best = ''
    for t in (110, 150, 90, 170):
        _, th = cv2.threshold(mx, t, 255, cv2.THRESH_BINARY)
        txt = pytesseract.image_to_string(th, config='--psm 7').strip().lower()
        if 'etapa' in txt:
            after = txt.split('etapa', 1)[1].lstrip(': ').strip()
            after = re.sub(r'[^a-z0-9 ]', '', after)
            after = re.sub(r'\s+', ' ', after).strip()
            if len(after) > len(best):
                best = after
        if len(best) >= 6:
            break
    return best


def normalizar(txt):
    """Casa a leitura ruidosa do OCR com o vocabulario canonico.

    Trata separadamente o sufixo numerico: 'empilhagem 1' e 'empilhagem 2'
    sao classes DIFERENTES e nunca podem ser fundidas por similaridade.
    """
    if not txt:
        return None

    m = re.search(r'\b([12])\b', txt)
    num = m.group(1) if m else None
    base = re.sub(r'\b[12]\b', '', txt).strip()
    # descarta palavras-lixo que o OCR agrega ("fim ee so", "espera y")
    palavras = [w for w in base.split() if len(w) >= 3]
    base = palavras[0] if palavras else base

    melhor, score = None, 0.0
    for v in VOCAB:
        vm = re.search(r'\b([12])\b', v)
        v_num = vm.group(1) if vm else None
        v_base = re.sub(r'\b[12]\b', '', v).strip()

        if v_num != num:          # sufixo tem de bater exatamente
            continue
        s = SequenceMatcher(None, base, v_base).ratio()
        if s > score:
            melhor, score = v, s

    return melhor if score >= 0.65 else None


def extrair_timeline():
    """Passada 1: le o rotulo a cada OCR_STRIDE frames."""
    cap = cv2.VideoCapture(VIDEO)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    timeline, brutos = {}, {}
    i = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if i % OCR_STRIDE == 0:
            raw = ocr_etapa(frame)
            brutos[i] = raw
            timeline[i] = normalizar(raw)
        i += 1
    cap.release()
    return timeline, brutos, total


def suavizar(timeline):
    """Filtro de mediana temporal: corrige leituras isoladas erradas.

    Uma etapa dura segundos; um rotulo que aparece sozinho entre dois
    vizinhos iguais e quase certamente erro de OCR.
    """
    chaves = sorted(timeline.keys())
    out = dict(timeline)
    for idx in range(1, len(chaves) - 1):
        ant, cur, prox = chaves[idx - 1], chaves[idx], chaves[idx + 1]
        if timeline[ant] and timeline[ant] == timeline[prox] and timeline[cur] != timeline[ant]:
            out[cur] = timeline[ant]
    return out


# ----------------------------------------------------------------------
# 2. LIMPEZA DO FRAME (remocao dos overlays)
# ----------------------------------------------------------------------
_MASK_ESTATICA = None


def construir_mascara_estatica(n_amostras=80):
    """Descobre automaticamente as LINHAS de overlay desenhadas pela camera.

    Ideia: as bounding boxes da Hikvision sao (a) estaticas no tempo,
    (b) de cor pura/saturada ou branco puro e (c) tracos FINOS. A cena real
    tem partes estaticas tambem (chao, caixas), mas sao blocos GROSSOS -
    entao um filtro morfologico de espessura separa as duas coisas.
    """
    cap = cv2.VideoCapture(VIDEO)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in np.linspace(0, n - 1, n_amostras).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ret, f = cap.read()
        if ret:
            frames.append(f.astype(np.float32))
    cap.release()

    stack = np.stack(frames)
    std = stack.std(axis=0).mean(axis=2)              # variancia temporal
    med = np.median(stack, axis=0).astype(np.uint8)

    hsv = cv2.cvtColor(med, cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1].astype(np.int16), hsv[:, :, 2].astype(np.int16)

    estatico = std < 6
    colorido = (sat > 140) & (val > 150)
    branco = (sat < 60) & (val > 200)
    mask = (estatico & (colorido | branco)).astype(np.uint8) * 255

    # mantem so estruturas FINAS (linhas), descarta blocos da cena real
    grosso = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    fino = cv2.subtract(mask, grosso)
    fino = cv2.morphologyEx(fino, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(fino, 8)
    out = np.zeros_like(fino)
    for i in range(1, nlab):
        if stats[i, cv2.CC_STAT_AREA] >= 25:
            out[lab == i] = 255
    return cv2.dilate(out, np.ones((3, 3), np.uint8), 1)


def mascara_caixas_rotulo(frame):
    """Detecta as tarjas escuras com texto das deteccoes da camera.

    Sao os rotulos tipo "Soldering iron 0.65" / "Donuts Stack 0.93".
    Diferente das linhas estaticas, ELES SE MOVEM junto com o objeto
    detectado - ou seja, correlacionam com a etapa e VAZAM rotulo.
    """
    mx = frame.max(axis=2)
    dark = (mx < 75).astype(np.uint8) * 255
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3, 21), np.uint8))

    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    out = np.zeros(frame.shape[:2], np.uint8)
    for i in range(1, nlab):
        x, y, w, h, a = stats[i]
        if 25 <= w <= 280 and 7 <= h <= 32 and w / h > 2.0 and y < 400:
            out[max(0, y - 2):y + h + 2, max(0, x - 2):x + w + 2] = 255
    return out


def limpar_frame(frame):
    """Remove overlay de texto + painel + linhas e tarjas de analytics."""
    global _MASK_ESTATICA
    if _MASK_ESTATICA is None:
        _MASK_ESTATICA = construir_mascara_estatica()

    out = frame.copy()

    # (a) inpainting: linhas estaticas das bounding boxes + tarjas moveis
    mask = cv2.bitwise_or(_MASK_ESTATICA, mascara_caixas_rotulo(out))
    if mask.any():
        out = cv2.inpaint(out, mask, 3, cv2.INPAINT_TELEA)

    # (b) blocos de overlay -> cinza neutro constante.
    #     Cinza constante nao carrega informacao de classe (identico em
    #     todos os frames), diferente do texto original.
    for (x1, y1, x2, y2) in OVERLAY_BOXES:
        cv2.rectangle(out, (x1, y1), (x2, y2), (128, 128, 128), -1)

    return out


# ----------------------------------------------------------------------
# 3. SEGMENTACAO TEMPORAL (evita vazamento treino/teste)
# ----------------------------------------------------------------------
def construir_segmentos(timeline, total):
    """Agrupa frames consecutivos de mesmo rotulo em segmentos.

    Frames vizinhos de video sao quase identicos. Se o split for por frame,
    o mesmo instante aparece em treino e teste e a acuracia fica inflada.
    Por isso o split e feito por SEGMENTO inteiro.
    """
    chaves = sorted(timeline.keys())
    segmentos = []
    atual, inicio = None, None

    for k in chaves:
        lab = timeline[k]
        if lab != atual:
            if atual is not None and inicio is not None:
                segmentos.append([inicio, k - 1, atual])
            atual, inicio = lab, k
    if atual is not None:
        segmentos.append([inicio, total - 1, atual])

    return [s for s in segmentos if s[2] and (s[1] - s[0]) >= OCR_STRIDE * 2]


def dividir_treino_teste(segmentos):
    """Aloca SEGMENTOS inteiros ate atingir ~TEST_RATIO dos frames da classe.

    Sempre mantem pelo menos um segmento no treino. Se a classe tiver um
    unico segmento, ele e cortado no tempo (nao embaralhado).
    """
    por_classe = defaultdict(list)
    for s in segmentos:
        por_classe[s[2]].append(s)

    train, test = [], []
    for classe, segs in por_classe.items():
        segs = sorted(segs, key=lambda s: s[0])
        total_frames = sum(s[1] - s[0] + 1 for s in segs)
        alvo = total_frames * TEST_RATIO

        if len(segs) == 1:
            ini, fim, lab = segs[0]
            corte = int(fim - (fim - ini) * TEST_RATIO)
            train.append([ini, corte, lab])
            test.append([corte + 1, fim, lab])
            continue

        ordenados = sorted(segs, key=lambda s: s[1] - s[0])   # menores primeiro
        acum, escolhidos = 0, set()
        for s in ordenados:
            n = s[1] - s[0] + 1
            if len(escolhidos) < len(segs) - 1 and acum + n <= alvo * 1.35:
                escolhidos.add(id(s))
                acum += n
        if not escolhidos:
            escolhidos.add(id(ordenados[0]))

        for s in segs:
            (test if id(s) in escolhidos else train).append(s)

    return train, test


# ----------------------------------------------------------------------
# 4. EXTRACAO DOS FRAMES LIMPOS
# ----------------------------------------------------------------------
def slug(txt):
    return re.sub(r'[^a-z0-9]+', '_', txt.lower()).strip('_')


def extrair_dataset(train_segs, test_segs):
    mapa = {}
    for (ini, fim, lab) in train_segs:
        for f in range(ini, fim + 1):
            mapa[f] = ('train', lab)
    for (ini, fim, lab) in test_segs:
        for f in range(ini, fim + 1):
            mapa[f] = ('test', lab)

    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)

    cap = cv2.VideoCapture(VIDEO)
    i, contagem = 0, defaultdict(int)
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if i in mapa and i % SAVE_STRIDE == 0:
            split, lab = mapa[i]
            limpo = limpar_frame(frame)
            x1, y1, x2, y2 = ROI
            img = cv2.resize(limpo[y1:y2, x1:x2], (IMG_SIZE, IMG_SIZE),
                             interpolation=cv2.INTER_AREA)
            d = os.path.join(OUT_DIR, split, slug(lab))
            os.makedirs(d, exist_ok=True)
            cv2.imwrite(os.path.join(d, f'frame_{i:05d}.jpg'), img,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            contagem[(split, lab)] += 1
        i += 1
    cap.release()
    return contagem


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
if __name__ == '__main__':
    print('[1/4] Lendo rotulos do overlay via OCR...')
    timeline, brutos, total = extrair_timeline()
    lidos = sum(1 for v in timeline.values() if v)
    print(f'      {lidos}/{len(timeline)} leituras validas '
          f'({100*lidos/len(timeline):.1f}%)')
    timeline = suavizar(timeline)
    print('      distribuicao:', dict(Counter(v for v in timeline.values() if v)))

    print('[2/4] Construindo segmentos temporais...')
    segmentos = construir_segmentos(timeline, total)
    print(f'      {len(segmentos)} segmentos validos')
    for s in segmentos:
        print(f'        {s[0]:>5}-{s[1]:<5} ({(s[1]-s[0])/30:>5.1f}s)  {s[2]}')

    print('[3/4] Dividindo treino/teste por segmento...')
    train_segs, test_segs = dividir_treino_teste(segmentos)
    print(f'      treino: {len(train_segs)} seg | teste: {len(test_segs)} seg')

    print('[4/4] Extraindo frames limpos...')
    contagem = extrair_dataset(train_segs, test_segs)

    print('\n=== RESULTADO ===')
    tot = defaultdict(lambda: [0, 0])
    for (split, lab), n in contagem.items():
        tot[lab][0 if split == 'train' else 1] = n
    print(f'{"classe":<18}{"treino":>8}{"teste":>8}{"%teste":>8}')
    for lab, (a, b) in sorted(tot.items()):
        pct = 100 * b / (a + b) if (a + b) else 0
        print(f'{lab:<18}{a:>8}{b:>8}{pct:>7.0f}%')
    ta, tb = sum(v[0] for v in tot.values()), sum(v[1] for v in tot.values())
    print(f'{"TOTAL":<18}{ta:>8}{tb:>8}{100*tb/(ta+tb):>7.0f}%')

    with open('relatorio_dataset.json', 'w') as fp:
        json.dump({
            'vocabulario': VOCAB,
            'ocr_validas_pct': round(100 * lidos / len(timeline), 1),
            'segmentos': [{'ini': s[0], 'fim': s[1], 'classe': s[2]} for s in segmentos],
            'train_segmentos': [{'ini': s[0], 'fim': s[1], 'classe': s[2]} for s in train_segs],
            'test_segmentos': [{'ini': s[0], 'fim': s[1], 'classe': s[2]} for s in test_segs],
            'contagem': {f'{k[0]}/{k[1]}': v for k, v in contagem.items()},
        }, fp, indent=2, ensure_ascii=False)
    print('\nrelatorio_dataset.json salvo.')
