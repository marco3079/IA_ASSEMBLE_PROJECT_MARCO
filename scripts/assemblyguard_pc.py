# -*- coding: utf-8 -*-
"""
AssemblyGuard PC — roda o modelo FOMO treinado no Edge Impulse em Python,
usando a camera Hikvision (RTSP), um arquivo de video ou uma webcam.

Vantagem estrategica: o modelo foi TREINADO com frames da Hikvision, entao
inferir na propria Hikvision elimina o domain shift (risco n.1 do plano).

Requisitos (uma vez):
    pip install opencv-python numpy tensorflow
    (ou, no lugar do tensorflow: pip install tflite-runtime, se disponivel)

Modelo: no Edge Impulse Studio -> Dashboard -> "Download block output" ->
    "Object detection model - TensorFlow Lite (int8 quantized)"
    Salve como: modelo_assemblyguard_int8.tflite (ou use --model)

Exemplos:
    # camera Hikvision (sub-stream 102 tem menos atraso que o 101):
    python assemblyguard_pc.py --source "rtsp://usuario:senha@192.168.0.64:554/Streaming/Channels/102"

    # validar com o video da sessao C (mesmo material do treino):
    python assemblyguard_pc.py --source "videos/WhatsApp Video 2026-08-12 at 15.43.32.mp4"

    # webcam do notebook (so para teste rapido; dominio diferente do treino):
    python assemblyguard_pc.py --source 0

Teclas na janela: q = sair · p = pausa · s = salvar screenshot
"""

import argparse
import csv
import os
import sys
import time

import cv2
import numpy as np

try:  # interpretador TFLite: tensorflow (Windows) ou tflite-runtime (leve)
    from tensorflow.lite.python.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        sys.exit("Instale um interpretador: pip install tensorflow  "
                 "(ou: pip install tflite-runtime)")

# ---------------------------------------------------------------------
# PARAMETROS — mesmos nomes e valores do firmware AssemblyGuard_XIAO.ino
# ---------------------------------------------------------------------
LABELS = ["ferro_solda", "peca", "pilha"]   # ordem alfabetica = ordem do EI
CONF_MIN = 0.60
N_CONFIRMA = 3

# ROI do treino (pipeline_deteccao.py): fracoes do frame-fonte da Hikvision
ROI_TREINO = (95 / 854, 25 / 480, 854 / 854, 445 / 480)
CAM_W, CAM_H = 320, 240                     # resolucao de coleta do dataset

# zonas em fracao do frame 320x240 (calibrar aqui e depois copiar p/ o .ino)
ZONAS = {
    "entrada": (0.72, 0.05, 1.00, 0.60),    # caixa de origem (direita)
    "bancada": (0.25, 0.25, 0.72, 0.80),    # area de trabalho
    "descanso_ferro": (0.05, 0.55, 0.25, 0.90),
    "saida": (0.00, 0.15, 0.20, 0.55),      # pilha pronta (esquerda)
}

ETAPAS = ["espera", "preparo", "montagem", "solda", "empilhagem", "fim"]


def dentro(zona, cx, cy):
    x1, y1, x2, y2 = ZONAS[zona]
    return x1 <= cx <= x2 and y1 <= cy <= y2


def proxima(etapa):
    i = ETAPAS.index(etapa)
    return ETAPAS[0] if etapa == "fim" else ETAPAS[i + 1]


# ---------------------------------------------------------------------
# MODELO — carga e decodificacao da saida do FOMO
# ---------------------------------------------------------------------
class Fomo:
    def __init__(self, caminho):
        self.itp = Interpreter(model_path=caminho)
        self.itp.allocate_tensors()
        self.inp = self.itp.get_input_details()[0]
        self.out = self.itp.get_output_details()[0]
        _, self.in_h, self.in_w, self.in_c = self.inp["shape"]
        print(f"modelo: entrada {self.in_w}x{self.in_h}x{self.in_c} "
              f"{self.inp['dtype'].__name__}, saida {list(self.out['shape'])}")

    def prepara(self, bgr):
        """320x240 BGR -> tensor de entrada (squash + grayscale, como no treino)."""
        img = cv2.resize(bgr, (self.in_w, self.in_h),
                         interpolation=cv2.INTER_AREA)          # squash
        if self.in_c == 1:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[..., None]
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        x = img.astype(np.float32) / 255.0                       # dominio 0..1
        if self.inp["dtype"] == np.int8:
            scale, zp = self.inp["quantization"]
            x = np.clip(np.round(x / scale + zp), -128, 127).astype(np.int8)
        return x[None, ...]

    def detecta(self, bgr, conf_min=CONF_MIN):
        """Retorna [(label, conf, cx, cy)] com cx,cy em fracao 0..1."""
        self.itp.set_tensor(self.inp["index"], self.prepara(bgr))
        self.itp.invoke()
        y = self.itp.get_tensor(self.out["index"])[0]            # (gh, gw, C)
        if self.out["dtype"] == np.int8:
            scale, zp = self.out["quantization"]
            y = (y.astype(np.float32) - zp) * scale
        gh, gw, nc = y.shape
        # FOMO: canal 0 = fundo quando ha classes+1 canais
        base = 1 if nc == len(LABELS) + 1 else 0
        achados = []
        for c, label in enumerate(LABELS):
            mapa = y[:, :, base + c]
            mask = (mapa >= conf_min).astype(np.uint8)
            if not mask.any():
                continue
            n, comp = cv2.connectedComponents(mask)
            for k in range(1, n):                    # agrupa celulas vizinhas
                ys, xs = np.nonzero(comp == k)
                pesos = mapa[ys, xs]
                cx = float((xs + 0.5) @ pesos / pesos.sum() / gw)
                cy = float((ys + 0.5) @ pesos / pesos.sum() / gh)
                achados.append((label, float(pesos.max()), cx, cy))
        return achados


# ---------------------------------------------------------------------
# LEITURA DO FRAME -> ETAPA (portado 1:1 do firmware)
# ---------------------------------------------------------------------
def inferir_leitura(achados):
    L = {"pecas_bancada": 0, "ferro_em_uso": False, "pilha_entrada": False,
         "pilha_bancada": False, "pilha_saida": False, "alvo": (-1.0, -1.0)}
    for label, conf, cx, cy in achados:
        if label == "peca":
            if dentro("bancada", cx, cy):
                L["pecas_bancada"] += 1
        elif label == "ferro_solda":
            if not dentro("descanso_ferro", cx, cy):
                L["ferro_em_uso"] = True
                L["alvo"] = (cx, cy)
        elif label == "pilha":
            if dentro("entrada", cx, cy):
                L["pilha_entrada"] = True
            elif dentro("saida", cx, cy):
                L["pilha_saida"] = True
                L["alvo"] = (cx, cy)
            else:
                L["pilha_bancada"] = True
                L["alvo"] = (cx, cy)

    if L["pilha_saida"]:          etapa = "fim"
    elif L["pilha_bancada"]:      etapa = "empilhagem"
    elif L["ferro_em_uso"]:       etapa = "solda"
    elif L["pecas_bancada"] >= 3: etapa = "montagem"
    elif L["pecas_bancada"] >= 1: etapa = "preparo"
    else:                         etapa = "espera"
    return L, etapa


# ---------------------------------------------------------------------
# VISUAL
# ---------------------------------------------------------------------
COR = {"peca": (255, 160, 40), "pilha": (40, 200, 255),
       "ferro_solda": (60, 60, 230)}

def desenha(quadro, achados, estado, leitura, L, lat_ms, ciclos, alerta):
    h, w = quadro.shape[:2]
    for nome, (x1, y1, x2, y2) in ZONAS.items():
        p1, p2 = (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h))
        cv2.rectangle(quadro, p1, p2, (90, 90, 90), 1)
        cv2.putText(quadro, nome, (p1[0] + 3, p1[1] + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (140, 140, 140), 1)
    for label, conf, cx, cy in achados:
        p = (int(cx * w), int(cy * h))
        cv2.circle(quadro, p, 7, COR[label], 2)
        cv2.putText(quadro, f"{label} {conf:.2f}", (p[0] + 8, p[1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, COR[label], 1)
    faixa = (0, 0, 200) if alerta else (40, 40, 40)
    cv2.rectangle(quadro, (0, h - 26), (w, h), faixa, -1)
    cv2.putText(quadro,
                f"estado={estado} leitura={leitura} pecas={L['pecas_bancada']} "
                f"lat={lat_ms:.0f}ms ciclos={ciclos}",
                (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)
    return quadro


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="AssemblyGuard no PC (Hikvision/video)")
    ap.add_argument("--model", default="modelo_assemblyguard_int8.tflite")
    ap.add_argument("--source", default="0",
                    help="RTSP da Hikvision, caminho de video ou indice de webcam")
    ap.add_argument("--conf", type=float, default=CONF_MIN)
    ap.add_argument("--sem-roi", action="store_true",
                    help="nao aplicar o recorte do treino (webcam/en enquadramento novo)")
    ap.add_argument("--csv", default="assemblyguard_pc_log.csv")
    ap.add_argument("--zoom", type=int, default=3, help="escala da janela")
    args = ap.parse_args()

    if not os.path.isfile(args.model):
        sys.exit(f"modelo nao encontrado: {args.model}\n"
                 "Baixe no Studio: Dashboard -> Download block output -> "
                 "Object detection model (int8 quantized)")

    fomo = Fomo(args.model)

    fonte = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(fonte)
    if not cap.isOpened():
        sys.exit(f"nao abriu a fonte de video: {args.source}")

    novo_csv = not os.path.isfile(args.csv)
    flog = open(args.csv, "a", newline="", encoding="utf-8")
    wlog = csv.writer(flog)
    if novo_csv:
        wlog.writerow(["ms", "estado", "leitura", "pecas_bancada",
                       "ferro_em_uso", "pilha_entrada", "pilha_bancada",
                       "pilha_saida", "alvo_x", "alvo_y", "latencia_ms", "alerta"])

    estado, leitura_ant, frames_iguais, ciclos = "espera", "espera", 0, 0
    t_ciclo = time.time()
    pausado = False

    while True:
        if not pausado:
            ok, frame = cap.read()
            if not ok:
                print("fim do video / fonte caiu")
                break

            if not args.sem_roi:                    # mesmo recorte do treino
                H, W = frame.shape[:2]
                x1, y1, x2, y2 = ROI_TREINO
                frame = frame[int(y1 * H):int(y2 * H), int(x1 * W):int(x2 * W)]
            quadro = cv2.resize(frame, (CAM_W, CAM_H),
                                interpolation=cv2.INTER_AREA)

            t0 = time.time()
            achados = fomo.detecta(quadro, args.conf)
            lat = (time.time() - t0) * 1000

            L, leitura = inferir_leitura(achados)

            frames_iguais = frames_iguais + 1 if leitura == leitura_ant else 1
            leitura_ant = leitura

            alerta = False
            if frames_iguais >= N_CONFIRMA and leitura != estado:
                if leitura == proxima(estado):
                    estado = leitura
                    print(f"== etapa: {estado}")
                    if estado == "fim":
                        ciclos += 1
                        print(f"== CICLO {ciclos} COMPLETO em "
                              f"{time.time() - t_ciclo:.1f} s")
                        t_ciclo = time.time()
                elif leitura != "espera":
                    alerta = True
                    print(f">>> ALERTA: esperado '{proxima(estado)}', "
                          f"detectado '{leitura}' em "
                          f"({L['alvo'][0]:.2f}, {L['alvo'][1]:.2f})")

            wlog.writerow([int(time.time() * 1000), estado, leitura,
                           L["pecas_bancada"], int(L["ferro_em_uso"]),
                           int(L["pilha_entrada"]), int(L["pilha_bancada"]),
                           int(L["pilha_saida"]),
                           f"{L['alvo'][0]:.2f}", f"{L['alvo'][1]:.2f}",
                           f"{lat:.0f}", int(alerta)])

            tela = desenha(quadro.copy(), achados, estado, leitura, L,
                           lat, ciclos, alerta)
            tela = cv2.resize(tela, (CAM_W * args.zoom, CAM_H * args.zoom),
                              interpolation=cv2.INTER_NEAREST)
            cv2.imshow("AssemblyGuard PC", tela)

        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("p"):
            pausado = not pausado
        if k == ord("s"):
            nome = f"screenshot_{int(time.time())}.jpg"
            cv2.imwrite(nome, tela)
            print(f"salvo {nome}")

    cap.release()
    flog.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
