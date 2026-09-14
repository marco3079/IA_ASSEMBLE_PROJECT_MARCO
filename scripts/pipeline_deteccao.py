#!/usr/bin/env python3
"""
AssemblyGuard V2 - Extracao de frames para DETECCAO DE OBJETOS (FOMO)
=====================================================================
Mudanca de rumo em relacao ao pipeline_assemblyguard.py (classificacao):
aqui nao rotulamos ETAPAS, rotulamos OBJETOS (peca, pilha, ferro_solda)
desenhando caixas no Edge Impulse. Logo:

  - nada de OCR;
  - nada de blocos cinza destruindo a imagem;
  - split por SESSAO, que e o padrao-ouro pedido pelo mentor:

        treino = video C (operador,  14.4 min, SEM overlay nenhum)
        teste  = video B (operadora,  4.1 min, overlays da Hikvision
                          removidos por inpainting)

  O video A (o que gerou o dataset v1) e o proprio B com o texto
  "Etapa: X" sobreposto pelo time - mesmo material, frame a frame.
  Com o B em maos, o A nao e mais necessario.

Saida:
  anotar/train/C_fNNNNN_tSSSs.jpg   (320x240)
  anotar/test/B_fNNNNN_tSSSs.jpg    (320x240)
  anotar/frames_deteccao.json       mapa frame -> video -> split
  anotar/amostra_train.jpg / amostra_test.jpg   conferencia visual

Uso:
  python pipeline_deteccao.py --preview   so gera os previews de ROI
  python pipeline_deteccao.py             extrai tudo
"""

import cv2
import numpy as np
import json
import os
import sys

import pipeline_assemblyguard as ag   # reuso: mascaras de limpeza do video B

OUT_DIR = 'anotar'
OUT_W, OUT_H = 320, 240   # resolucao de coleta do livro do Rovai
JPEG_Q = 92

# Coordenadas em FRACAO do frame (x1, y1, x2, y2) - os videos tem
# resolucoes diferentes (848x478 e 854x480).
VIDEOS = {
    'C': {
        'path': r'D:\WhatsApp Video 2026-08-12 at 15.43.32.mp4',
        'split': 'train',
        'alvo': 140,
        # mantem a caixa de origem (alto a direita), a bandeja da direita
        # e a zona de saida a esquerda - o ciclo inteiro no enquadramento
        'roi': (95 / 854, 25 / 480, 854 / 854, 445 / 480),
        # unicos overlays do C: relogio, logo HIKVISION e rodape (estaticos)
        'inpaint': [
            (0 / 854, 0 / 480, 345 / 854, 46 / 480),      # timestamp (fonte
            #  maior que no B: ate y=46, senao sobram digitos meio-cortados)
            (575 / 854, 18 / 480, 745 / 854, 52 / 480),   # logo HIKVISION
            (380 / 854, 390 / 480, 735 / 854, 430 / 480), # rodape modelo
        ],
        'mask_estatica': False,   # C nao tem analytics
        'tarjas_moveis': False,
    },
    'B': {
        'path': r'D:\WhatsApp Video 2026-08-12 at 15.39.00.mp4',
        'split': 'test',
        'alvo': 60,
        'roi': (95 / 854, 25 / 480, 854 / 854, 445 / 480),
        'inpaint': [
            (0 / 848, 0 / 478, 345 / 848, 32 / 478),      # timestamp
            (610 / 816, 0, 1.0, 175 / 464),               # painel Hikvision
            (480 / 816, 392 / 464, 1.0, 432 / 464),       # rodape
            (604 / 848, 156 / 478, 692 / 848, 178 / 478), # chip "Donuts" fixo
        ],                                                #  no canto da zona
        'mask_estatica': True,    # linhas coloridas das zonas (estaticas)
        'tarjas_moveis': True,    # caixas pretas "Donuts Stack 0.95" (moveis)
    },
}


def px(frac_box, w, h):
    x1, y1, x2, y2 = frac_box
    return (int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h))


# Tarjas de rotulo da Hikvision ("Glue Gun 0.84"...): quatro geracoes de
# detector morfologico falharam neste encode (fundo preto que se desintegra
# no opening; texto que ora fragmenta, ora se funde com objetos brancos
# reais conforme o limiar). A saida robusta e outra: a camera desenha
# SEMPRE os mesmos tres textos, na mesma fonte e tamanho, entao template
# matching e praticamente exato. Os templates saem do proprio video B
# (frame 1240, posicoes conferidas visualmente).
_TPL_SRC_FRAME = 1240
_TPL_BOXES = {                      # (y1, y2, x1, x2) no frame 848x478
    'donuts_stack': (212, 226, 605, 661),
    'glue_gun': (299, 313, 241, 287),
    'soldering_iron': (320, 334, 332, 406),
}
# limiar por template: a tarja e semitransparente e a correlacao cai
# conforme o fundo atras dela; calibrado olhando a folha de amostra dos
# 60 frames de teste (limiar alto deixa tarja sobre as pilhas em
# movimento; baixo demais mancha a camisa da operadora)
_TPL_THR = {'donuts_stack': 0.46, 'glue_gun': 0.55, 'soldering_iron': 0.52}
_TEMPLATES = None


def _carregar_templates(path):
    global _TEMPLATES
    cap = cv2.VideoCapture(path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, _TPL_SRC_FRAME)
    ret, f = cap.read()
    cap.release()
    if not ret:
        raise SystemExit('nao consegui ler o frame dos templates')
    g = f.min(axis=2).astype(np.float32)
    _TEMPLATES = {nome: g[y1:y2, x1:x2]
                  for nome, (y1, y2, x1, x2) in _TPL_BOXES.items()}


def tarjas_pretas(frame):
    """Acha as tarjas por correlacao com os templates dos textos.

    A mascara cobre o template mais uma folga a direita para o sufixo
    de score (" 0.84"), que varia frame a frame.
    """
    assert _TEMPLATES is not None, 'chame _carregar_templates antes'
    g = frame.min(axis=2).astype(np.float32)
    out = np.zeros(frame.shape[:2], np.uint8)
    H, W = out.shape
    for nome, tpl in _TEMPLATES.items():
        th, tw = tpl.shape
        res = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= _TPL_THR[nome])
        for y, x in zip(ys, xs):
            out[max(0, y - 4):min(H, y + th + 4),
                max(0, x - 4):min(W, x + tw + int(tw * 0.6) + 8)] = 255
    return out


# Nota: um detector de contornos pretos finos foi testado e descartado -
# a grade do ventilador e feita exatamente de linhas escuras finas e o
# inpainting a transformava em borrao. Os contornos de 1-2 px que a camera
# desenha somem sozinhos no resize para 96x96.


def mascara_linhas_overlay(path, n_amostras=60):
    """Linhas e textos coloridos das zonas, extraidos da MEDIANA temporal.

    A mascara estatica classica (std<6) so pega os trechos sobre fundo
    parado; os trechos desenhados POR CIMA da operadora tem std alto e
    escapam. Mas a POSICAO das linhas e fixa: basta detecta-las na
    imagem mediana - onde aparecem inteiras, pois sao desenhadas por
    cima de tudo - e apagar sempre esses pixels, em todos os frames.
    Isso tambem elimina o ruido do detector por-frame (glints e reflexos
    saturados da cena real nao sobrevivem a mediana de 60 frames).
    """
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in np.linspace(0, n - 1, n_amostras).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ret, f = cap.read()
        if ret:
            frames.append(f)
    cap.release()

    med = np.median(np.stack(frames), axis=0).astype(np.uint8)
    hsv = cv2.cvtColor(med, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    # verde frouxo (a linha alfa-misturada perde saturacao sobre areas
    # claras; verde puro nao existe na cena real); azul apertado para
    # nao tocar as pecas do Bloq Volt
    verde = (h > 42) & (h < 88) & (s > 60) & (v > 90)
    verm = ((h < 12) | (h > 168)) & (s > 95) & (v > 100)
    azul = (h > 98) & (h < 132) & (s > 140) & (v > 140)
    cor = (verde | verm | azul).astype(np.uint8) * 255

    # so estruturas FINAS: blocos grossos (pecas, pistola) sobrevivem ao
    # opening e sao subtraidos
    grosso = cv2.morphologyEx(cor, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    fino = cv2.subtract(cor, grosso)
    fino = cv2.morphologyEx(fino, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(fino, 8)
    out = np.zeros(med.shape[:2], np.uint8)
    for i in range(1, nlab):
        if stats[i, cv2.CC_STAT_AREA] >= 20:
            out[lab == i] = 255
    return cv2.dilate(out, np.ones((3, 3), np.uint8), 1)


def abrir(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f'nao consegui abrir: {path}')
    return cap


def ler_frame(cap, idx):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
    ret, f = cap.read()
    return f if ret else None


def mascara_inpaint(cfg, frame, mask_estatica):
    """Une todas as regioes a apagar num unico inpaint por frame."""
    h, w = frame.shape[:2]
    mask = np.zeros((h, w), np.uint8)

    for box in cfg['inpaint']:
        x1, y1, x2, y2 = px(box, w, h)
        mask[y1:y2, x1:x2] = 255

    if mask_estatica is not None:
        mask = cv2.bitwise_or(mask, mask_estatica)

    if cfg['tarjas_moveis']:
        mask = cv2.bitwise_or(mask, tarjas_pretas(frame))

    return mask


def limpar(cfg, frame, mask_estatica):
    mask = mascara_inpaint(cfg, frame, mask_estatica)
    if mask.any():
        frame = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
    return frame


def preparar_mask_estatica(nome, cfg, shape):
    """Uniao das duas mascaras de posicao fixa do video B.

    ag.construir_mascara_estatica (reuso do pipeline v1) pega linhas
    sobre fundo parado; mascara_linhas_overlay, extraida da mediana,
    pega tambem os trechos desenhados por cima da operadora.
    """
    if not cfg['mask_estatica']:
        return None
    print(f'  [{nome}] construindo mascaras de overlay...')
    if cfg['tarjas_moveis']:
        _carregar_templates(cfg['path'])
    ag.VIDEO = cfg['path']
    m = ag.construir_mascara_estatica()
    if m.shape != shape[:2]:
        m = cv2.resize(m, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
    return cv2.bitwise_or(m, mascara_linhas_overlay(cfg['path']))


def indices_uniformes(n_total, alvo):
    """Um frame por janela de tempo, cobrindo o video inteiro.

    Com a camera fixa e o operador em movimento continuo, frames ~6s
    distantes ja sao suficientemente diversos; dedupe extra nao paga
    o proprio custo.
    """
    return np.linspace(0, n_total - 1, alvo).astype(int)


def preview(nome, cfg):
    cap = abrir(cfg['path'])
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f = ler_frame(cap, n // 2)
    cap.release()
    h, w = f.shape[:2]

    marcado = f.copy()
    for box in cfg['inpaint']:
        x1, y1, x2, y2 = px(box, w, h)
        cv2.rectangle(marcado, (x1, y1), (x2, y2), (0, 0, 255), 2)
    rx1, ry1, rx2, ry2 = px(cfg['roi'], w, h)
    cv2.rectangle(marcado, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
    cv2.putText(marcado, f'{nome} ROI {rx2-rx1}x{ry2-ry1} -> {OUT_W}x{OUT_H}',
                (rx1 + 6, ry1 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 255, 0), 2, cv2.LINE_AA)

    os.makedirs(OUT_DIR, exist_ok=True)
    dest = os.path.join(OUT_DIR, f'preview_roi_{nome}.jpg')
    cv2.imwrite(dest, marcado, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f'  {dest}')


def extrair(nome, cfg):
    cap = abrir(cfg['path'])
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    mask_est = preparar_mask_estatica(nome, cfg, (h, w))
    rx1, ry1, rx2, ry2 = px(cfg['roi'], w, h)

    d = os.path.join(OUT_DIR, cfg['split'])
    os.makedirs(d, exist_ok=True)

    registros = []
    for i in indices_uniformes(n, cfg['alvo']):
        f = ler_frame(cap, i)
        if f is None:
            continue
        f = limpar(cfg, f, mask_est)
        crop = f[ry1:ry2, rx1:rx2]
        img = cv2.resize(crop, (OUT_W, OUT_H), interpolation=cv2.INTER_AREA)
        arq = f'{nome}_f{i:05d}_t{i/fps:04.0f}s.jpg'
        cv2.imwrite(os.path.join(d, arq), img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        registros.append({'arquivo': arq, 'video': nome, 'frame': int(i),
                          't_seg': round(i / fps, 1), 'split': cfg['split']})
    cap.release()
    print(f'  [{nome}] {len(registros)} frames -> {d}/')
    return registros


def folha_amostra(split, n_amostras=12):
    d = os.path.join(OUT_DIR, split)
    arquivos = sorted(os.listdir(d))
    idxs = np.linspace(0, len(arquivos) - 1, min(n_amostras, len(arquivos))).astype(int)
    celulas = []
    for i in idxs:
        img = cv2.imread(os.path.join(d, arquivos[i]))
        cv2.rectangle(img, (0, 0), (OUT_W, 18), (0, 0, 0), -1)
        cv2.putText(img, arquivos[i], (4, 13), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (0, 255, 255), 1, cv2.LINE_AA)
        celulas.append(img)
    while len(celulas) % 4:
        celulas.append(np.zeros_like(celulas[0]))
    linhas = [np.hstack(celulas[i:i + 4]) for i in range(0, len(celulas), 4)]
    dest = os.path.join(OUT_DIR, f'amostra_{split}.jpg')
    cv2.imwrite(dest, np.vstack(linhas), [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f'  {dest}')


if __name__ == '__main__':
    so_preview = '--preview' in sys.argv

    print('[1/2] Previews de ROI...')
    for nome, cfg in VIDEOS.items():
        preview(nome, cfg)
    if so_preview:
        print('\nConfira os preview_roi_*.jpg antes de extrair.')
        sys.exit(0)

    print('[2/2] Extraindo frames...')
    todos = []
    for nome, cfg in VIDEOS.items():
        todos += extrair(nome, cfg)

    for split in ('train', 'test'):
        folha_amostra(split)

    with open(os.path.join(OUT_DIR, 'frames_deteccao.json'), 'w') as fp:
        json.dump({
            'classes_a_anotar': ['peca', 'pilha', 'ferro_solda'],
            'split_por_sessao': {
                'train': 'video C - operador, 2026-03-10, sem overlays',
                'test': 'video B - operadora, 2026-03-17, overlays inpaintados',
            },
            'frames': todos,
        }, fp, indent=2, ensure_ascii=False)

    print(f'\n{len(todos)} frames prontos em {OUT_DIR}/')
    print('Proximo passo: upload no Edge Impulse (train e test separados)')
    print('e anotar as caixas de: peca, pilha, ferro_solda.')
