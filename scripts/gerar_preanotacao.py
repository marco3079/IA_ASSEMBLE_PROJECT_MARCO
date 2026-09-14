#!/usr/bin/env python3
"""
AssemblyGuard V2 - Pre-anotacao automatica para o Edge Impulse
==============================================================
Gera caixas de `peca`, `pilha` e `ferro_solda` por segmentacao de cor
nos 200 frames de anotar/, no formato `bounding_boxes.labels` que o
uploader do Edge Impulse reconhece (as caixas ja entram desenhadas; a
Labeling queue vira revisao em vez de desenho).

Como separa as classes:
  - donuts/pilhas: azul ESCURO saturado (a bancada e azul-clara
    dessaturada, os cabos sao quase pretos -> ficam fora por S e V);
  - peca vs pilha: vista de cima a altura nao aparece, entao usamos
    (a) forma alongada (colunas deitadas no estoque) e
    (b) posicao - a camera e fixa e as zonas de estoque (bandeja a
        direita, caixa no alto a direita) e de saida (esquerda) so
        contem pilhas;
  - ferro_solda: unico objeto verde da cena (cabo do ferro).

REVISAR DEPOIS NA LABELING QUEUE - erros esperados e conhecidos:
  - pilha em formacao no MEIO da bancada comeca rotulada como peca;
  - ferro totalmente coberto pela mao some;
  - pecas na mao podem fundir num blob so.
"""

import cv2
import numpy as np
import json
import os

PASTAS = ['anotar/train', 'anotar/test']
W, H = 320, 240

# zonas de prior de classe, em fracao do frame (x1, y1, x2, y2)
ZONAS_PILHA = [
    (0.60, 0.00, 1.00, 0.33),   # caixa de estoque (alto, direita)
    (0.80, 0.25, 1.00, 0.95),   # bandeja/prateleira da direita
    (0.00, 0.20, 0.14, 0.70),   # zona de saida (esquerda)
]

AREA_MIN = 90         # blob menor que isso e ruido/reflexo
AREA_MIN_FERRO = 60

# limites de uma PECA individual (donut ~30px de diametro em 320x240);
# blobs esparsos (bordas da bancada) caem no fill minimo
PECA_MAX_LADO = 95
PECA_MAX_AREA = 3500
PECA_FILL_MIN = 0.42

# pilhas podem ser blobs grandes (estoque inteiro da caixa vira um blob)
PILHA_MAX_LADO = 175


def em_zona_pilha(cx, cy):
    fx, fy = cx / W, cy / H
    return any(x1 <= fx <= x2 and y1 <= fy <= y2
               for (x1, y1, x2, y2) in ZONAS_PILHA)


def caixas_da_imagem(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    caixas = []

    # ---- donuts / pilhas: azul escuro saturado ----
    azul = ((h > 100) & (h < 132) & (s > 120) & (v > 55) & (v < 230))
    azul = (azul.astype(np.uint8)) * 255
    # open 5x5: separa donuts encostados (tocam-se por istmos finos) e
    # apaga as linhas azuis da borda da bancada, que sao tracos de ~3px
    azul = cv2.morphologyEx(azul, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    azul = cv2.morphologyEx(azul, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    # ---- PECAS por circulo de Hough: o donut e um ANEL, e circulos
    # ignoram por construcao as bordas retas da bancada que enganavam o
    # filtro por blob ----
    blur = cv2.GaussianBlur(azul, (5, 5), 0)
    circ = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=17,
                            param1=110, param2=13, minRadius=8, maxRadius=19)
    if circ is not None:
        for cx, cy, r in circ[0]:
            x1, y1 = int(cx - r), int(cy - r)
            x2, y2 = int(cx + r), int(cy + r)
            # o circulo precisa estar de fato preenchido de azul
            recorte = azul[max(0, y1):y2, max(0, x1):x2]
            if recorte.size == 0 or (recorte > 0).mean() < 0.45:
                continue
            label = 'pilha' if em_zona_pilha(cx, cy) else 'peca'
            caixas.append({'label': label, 'x': max(0, x1), 'y': max(0, y1),
                           'width': int(2 * r), 'height': int(2 * r)})

    # ---- PILHAS deitadas (colunas do estoque): blobs alongados ----
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(azul, 8)
    for i in range(1, nlab):
        x, y, w_, h_, a = stats[i]
        if a < AREA_MIN or max(w_, h_) > PILHA_MAX_LADO:
            continue
        cx, cy = cent[i]
        alongado = max(w_, h_) / max(1, min(w_, h_)) > 1.7
        if not (alongado and em_zona_pilha(cx, cy)):
            continue
        # nao duplicar o que os circulos ja cobriram
        ja = any(abs(c['x'] + c['width'] / 2 - cx) < 12 and
                 abs(c['y'] + c['height'] / 2 - cy) < 12 for c in caixas)
        if not ja:
            caixas.append({'label': 'pilha', 'x': int(x), 'y': int(y),
                           'width': int(w_), 'height': int(h_)})

    # ---- ferro de solda: cabo verde ----
    verde = ((h > 45) & (h < 90) & (s > 90) & (v > 70))
    verde = (verde.astype(np.uint8)) * 255
    verde = cv2.morphologyEx(verde, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(verde, 8)
    melhor = None
    for i in range(1, nlab):
        x, y, w_, h_, a = stats[i]
        if a >= AREA_MIN_FERRO and (melhor is None or a > melhor[4]):
            melhor = (x, y, w_, h_, a)
    if melhor:
        x, y, w_, h_, _ = melhor
        # a caixa cobre o cabo; expande um pouco para pegar a ponta
        caixas.append({'label': 'ferro_solda',
                       'x': max(0, int(x) - 6), 'y': max(0, int(y) - 6),
                       'width': min(W, int(w_) + 12),
                       'height': min(H, int(h_) + 12)})

    return caixas


def desenhar(img, caixas):
    cores = {'peca': (0, 255, 255), 'pilha': (255, 0, 255),
             'ferro_solda': (0, 255, 0)}
    out = img.copy()
    for c in caixas:
        cor = cores[c['label']]
        cv2.rectangle(out, (c['x'], c['y']),
                      (c['x'] + c['width'], c['y'] + c['height']), cor, 1)
        cv2.putText(out, c['label'][:5], (c['x'], max(8, c['y'] - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, cor, 1, cv2.LINE_AA)
    return out


if __name__ == '__main__':
    total_caixas = 0
    for pasta in PASTAS:
        arquivos = sorted(f for f in os.listdir(pasta) if f.endswith('.jpg'))
        labels = {'version': 1, 'type': 'bounding-box-labels',
                  'boundingBoxes': {}}
        amostras = []
        for idx, arq in enumerate(arquivos):
            img = cv2.imread(os.path.join(pasta, arq))
            caixas = caixas_da_imagem(img)
            labels['boundingBoxes'][arq] = caixas
            total_caixas += len(caixas)
            if idx % max(1, len(arquivos) // 12) == 0:
                amostras.append(desenhar(img, caixas))

        with open(os.path.join(pasta, 'bounding_boxes.labels'), 'w') as fp:
            json.dump(labels, fp)

        # folha de conferencia visual
        while len(amostras) % 4:
            amostras.append(np.zeros_like(amostras[0]))
        linhas = [np.hstack(amostras[i:i + 4]) for i in range(0, len(amostras), 4)]
        nome = pasta.split('/')[-1]
        cv2.imwrite(f'anotar/preanotacao_{nome}.jpg', np.vstack(linhas),
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f'{pasta}: {len(arquivos)} imagens anotadas '
              f'-> {pasta}/bounding_boxes.labels')

    print(f'total de caixas geradas: {total_caixas}')
    print('confira: anotar/preanotacao_train.jpg e preanotacao_test.jpg')
