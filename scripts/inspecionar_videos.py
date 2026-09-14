#!/usr/bin/env python3
"""
AssemblyGuard - Inspecao dos videos de origem
=============================================
Gate do passo 0: antes de extrair qualquer frame, precisamos responder
olhando para as imagens:

  1. O video B e o mesmo material do A com encode melhor, alinhado
     frame-a-frame? Se for, a linha do tempo de relatorio_dataset.json
     (feita sobre o A) transfere direto e ganhamos resolucao de graca.
  2. O video C e a mesma camera, mesmo angulo e mesmo layout de zonas?
     E a condicao para ele servir de test set por SESSAO.
  3. Onde ficam os overlays em cada resolucao? As caixas hoje sao fixas
     em pixels para 816x464 e os tres videos tem tamanhos diferentes.

Saida em inspecao/:
    contato_A.jpg, contato_B.jpg, contato_C.jpg   folhas de contato
    alinhamento_AB.jpg                            A x B no mesmo indice
    roi_<v>.jpg                                   corte proposto por video
"""

import cv2
import numpy as np
import os

OUT_DIR = 'inspecao'

VIDEOS = {
    'A': r'D:\WhatsApp Video 2026-08-21 at 08.29.02.mp4',
    'B': r'D:\WhatsApp Video 2026-08-12 at 15.39.00.mp4',
    'C': r'D:\WhatsApp Video 2026-08-12 at 15.43.32.mp4',
}

# ROI proposto e caixas de overlay, em FRACAO do tamanho do frame.
# Derivados das medidas validadas no video A (816x464):
#   ROI          (130, 55, 605, 391)
#   Etapa: X     (0, 0, 285, 52)
#   painel       (610, 0, 816, 175)
#   rodape       (480, 392, 816, 432)
ROI_FRAC = (130 / 816, 55 / 464, 605 / 816, 391 / 464)

OVERLAYS_FRAC = {
    'Etapa: X': (0 / 816, 0 / 464, 285 / 816, 52 / 464),
    'painel': (610 / 816, 0 / 464, 816 / 816, 175 / 464),
    'rodape': (480 / 816, 392 / 464, 816 / 816, 432 / 464),
}

N_CONTATO = 12          # frames por folha de contato
LARGURA_CELULA = 400    # px por celula da folha


def abrir(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f'nao consegui abrir: {path}')
    return cap


def ler_frame(cap, idx):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
    ret, f = cap.read()
    return f if ret else None


def rotular(img, texto):
    """Faixa preta com o texto no topo da celula."""
    out = img.copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 22), (0, 0, 0), -1)
    cv2.putText(out, texto, (5, 16), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return out


def grade(celulas, cols=4):
    """Empilha celulas de mesmo tamanho numa grade."""
    linhas = []
    for i in range(0, len(celulas), cols):
        fila = celulas[i:i + cols]
        while len(fila) < cols:
            fila.append(np.zeros_like(celulas[0]))
        linhas.append(np.hstack(fila))
    return np.vstack(linhas)


def redim(img, largura):
    h, w = img.shape[:2]
    return cv2.resize(img, (largura, int(h * largura / w)), interpolation=cv2.INTER_AREA)


def folha_de_contato(nome, path):
    cap = abrir(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    idxs = np.linspace(0, n - 1, N_CONTATO).astype(int)
    celulas = []
    for i in idxs:
        f = ler_frame(cap, i)
        if f is None:
            continue
        celulas.append(rotular(redim(f, LARGURA_CELULA),
                               f'{nome} f{i}  t={i/fps:.0f}s'))
    cap.release()

    dest = os.path.join(OUT_DIR, f'contato_{nome}.jpg')
    cv2.imwrite(dest, grade(celulas), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(f'  {dest}   ({w}x{h}, {n} frames, {n/fps:.1f}s)')
    return (w, h, n, fps)


def alinhamento_ab():
    """A e B tem a mesma duracao e o mesmo numero de frames.

    Se forem o mesmo material, os mesmos indices mostram a mesma cena.
    Coloca A em cima e B embaixo para conferir a olho.
    """
    ca, cb = abrir(VIDEOS['A']), abrir(VIDEOS['B'])
    n = min(int(ca.get(cv2.CAP_PROP_FRAME_COUNT)),
            int(cb.get(cv2.CAP_PROP_FRAME_COUNT)))

    pares = []
    for i in np.linspace(0, n - 1, 4).astype(int):
        fa, fb = ler_frame(ca, i), ler_frame(cb, i)
        if fa is None or fb is None:
            continue
        fa = rotular(redim(fa, 520), f'A f{i}  {fa.shape[1]}x{fa.shape[0]}')
        fb = rotular(redim(fb, 520), f'B f{i}  {fb.shape[1]}x{fb.shape[0]}')
        alt = min(fa.shape[0], fb.shape[0])
        pares.append(np.vstack([fa[:alt], fb[:alt]]))
    ca.release(); cb.release()

    dest = os.path.join(OUT_DIR, 'alinhamento_AB.jpg')
    cv2.imwrite(dest, grade(pares, cols=2), [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f'  {dest}   (A em cima, B embaixo, mesmo indice de frame)')


def preview_roi(nome, path):
    """Desenha o ROI proposto e as caixas de overlay sobre um frame real.

    O ponto a conferir: as tres caixas devem ficar INTEIRAS fora do
    retangulo verde. E o que torna os blocos cinza desnecessarios.
    """
    cap = abrir(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    f = ler_frame(cap, n // 3)
    cap.release()
    if f is None:
        return

    h, w = f.shape[:2]
    px = lambda fr: (int(fr[0] * w), int(fr[1] * h), int(fr[2] * w), int(fr[3] * h))

    marcado = f.copy()
    for rotulo, fr in OVERLAYS_FRAC.items():
        x1, y1, x2, y2 = px(fr)
        cv2.rectangle(marcado, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(marcado, rotulo, (x1 + 4, min(y2, h - 6) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)

    rx1, ry1, rx2, ry2 = px(ROI_FRAC)
    cv2.rectangle(marcado, (rx1, ry1), (rx2, ry2), (0, 255, 0), 2)
    cv2.putText(marcado, f'ROI {rx2-rx1}x{ry2-ry1}', (rx1 + 4, ry1 + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)

    corte = f[ry1:ry2, rx1:rx2]
    esq = rotular(redim(marcado, 620), f'{nome}  {w}x{h}  vermelho=overlay  verde=ROI')
    dir_ = rotular(redim(corte, 620), f'{nome}  corte {rx2-rx1}x{ry2-ry1}')
    alt = max(esq.shape[0], dir_.shape[0])
    pad = lambda im: np.vstack([im, np.zeros((alt - im.shape[0], im.shape[1], 3), np.uint8)])
    lado = np.hstack([pad(esq), pad(dir_)])

    dest = os.path.join(OUT_DIR, f'roi_{nome}.jpg')
    cv2.imwrite(dest, lado, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f'  {dest}')


if __name__ == '__main__':
    os.makedirs(OUT_DIR, exist_ok=True)

    print('[1/3] Folhas de contato...')
    metas = {nome: folha_de_contato(nome, p) for nome, p in VIDEOS.items()}

    print('[2/3] Alinhamento A x B...')
    alinhamento_ab()

    print('[3/3] ROI proposto por video...')
    for nome, p in VIDEOS.items():
        preview_roi(nome, p)

    print('\nAgora olhe as imagens em inspecao/ e responda:')
    print('  1. B e o mesmo material do A, mesmo instante no mesmo indice?')
    print('  2. C e a mesma camera / angulo / layout de zonas?')
    print('  3. Em cada video, as 3 caixas vermelhas ficam FORA do retangulo verde?')
