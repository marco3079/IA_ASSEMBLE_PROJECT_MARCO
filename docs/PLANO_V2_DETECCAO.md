# AssemblyGuard V2 — Detecção de Objetos (resposta ao feedback do mentor)

**Projeto:** AssemblyGuard — Monitoramento de montagem do Bloq Volt via TinyML
**Curso:** IESTI01 TinyML — CR018-2026_2026_S2_T01
**Hardware:** Seeed Studio XIAO ESP32S3 Sense (OV2640)
**Referência técnica:** [capítulo de Object Detection do livro do Prof. Rovai](https://mjrovai.github.io/TinyML_Made_Easy_XIAO_ESP32S3_ebook/content/xiaoml_kit/object_detection/object_detection.html)

Este documento responde ponto a ponto o feedback recebido e registra as
decisões da versão 2 do projeto.

---

## 1. Correção de hardware (apontada pelo mentor — confirmada)

A restrição de "~512 KB" no documento V1 estava errada. Especificação real:

| Recurso | XIAO ESP32S3 Sense |
|---|---|
| SoC | ESP32-S3R8, Xtensa LX7 dual-core @ 240 MHz |
| SRAM interna | 512 KB |
| **PSRAM** | **8 MB** (octal SPI) |
| **Flash** | **8 MB** |
| Câmera | OV2640, até 1600×1200 |
| microSD | até 32 GB, FAT32 (na placa Sense) |
| Rádio | Wi-Fi 2.4 GHz + BLE 5.0 |

Confrontando com o orçamento do FOMO (números do livro: ~250 KB de RAM,
~80 KB de flash, 143 ms/inferência, ~7 fps): **sobra folga larga**. No
firmware, o framebuffer da câmera é alocado na PSRAM
(`fb_location = CAMERA_FB_IN_PSRAM`).

Detalhe de hardware descoberto na implementação: o LED onboard e o chip
select do microSD compartilham o **GPIO21** — o firmware trata o conflito.

## 2. De classificação para detecção de objetos (mudança central)

O mentor está certo: classificador de frame inteiro diz *qual* etapa parece
estar acontecendo, mas nunca *onde* está o problema — e o objetivo do
AssemblyGuard é apontar desvio de posicionamento. A V2 usa **FOMO
(MobileNetV2 0.35)** no Edge Impulse, 96×96 grayscale, conforme o capítulo
do livro.

**Classes de objeto:** `peca` (o "donut" azul), `pilha` (peças empilhadas),
`ferro_solda`. A `mao` foi considerada e descartada: fica sobreposta a tudo
o tempo todo — exatamente a fraqueza documentada do FOMO. A `pilha` é
obrigatória porque **o ciclo do Bloq Volt é delimitado por ela**: começa
com a pilha na zona de entrada (direita) e termina quando a pilha pronta
aparece na zona de saída (esquerda).

A etapa deixa de ser "adivinhada" pela cena inteira e passa a ser **derivada
da posição dos objetos**:

```
FOMO 96×96 → centroides {peca, pilha, ferro_solda}
                │
    ┌───────────┴────────────┐
    ▼                        ▼
mapa de ZONAS         máquina de ESTADOS
(entrada / bancada /  (espera → preparo → montagem →
 ferro / saída)        solda → empilhagem → fim)
    └───────────┬────────────┘
                ▼
      ALERTA + posição (x, y)
                │
                └─► CSV + JPEG no microSD
```

Regras de derivação (implementadas em `AssemblyGuard_XIAO.ino`, caláveis):
`pilha` na saída → **fim** · `pilha` na bancada → **empilhagem** ·
`ferro_solda` fora do descanso → **solda** · ≥3 `peca` na bancada →
**montagem** · ≥1 → **preparo** · nada → **espera**.

## 3. Split do dataset (orientação do mentor — superada com folga)

A V1 fazia split por segmento temporal porque só havia um vídeo. Agora há
**duas sessões de gravação**, e o split é **por sessão E por operador**:

| Conjunto | Origem | Operador | Duração | Frames |
|---|---|---|---|---|
| **Treino** | Vídeo C (2026-03-10) | operador | 14,4 min | 140 |
| **Teste** | Vídeo B (2026-03-17) | operadora | 4,1 min | 60 |

Nenhum frame da sessão de teste compartilha instante, operador ou dia com o
treino. É o teste de generalização mais forte possível com o material atual.

Descobertas sobre o material que viabilizaram isso:

- O vídeo usado na V1 ("A") é o vídeo B **com o texto `Etapa: X` sobreposto
  pelo time** — mesmo conteúdo, frame a frame. Com o B em mãos, o vazamento
  principal da V1 simplesmente não existe.
- O vídeo C **não tem overlay nenhum** (sem analytics, sem texto): a fonte
  de treino é praticamente crua.
- Os overlays restantes do B (linhas de zona, tarjas "Donuts Stack 0.92")
  foram removidos por inpainting: máscara temporal por mediana para as
  linhas + *template matching* dos três textos das tarjas
  (`pipeline_deteccao.py`).
- Detalhe: para detecção de objetos, rotular etapa por OCR ficou
  desnecessário — anotamos objetos, não etapas. O custo de anotação caiu
  para ~200 imagens × 3 caixas.

## 4. Critérios de sucesso (faltavam — definidos agora)

| # | Critério | Meta | Como medir |
|---|---|---|---|
| 1 | F1 por classe (`peca`, `pilha`, `ferro_solda`) | ≥ 0,80 | Model Testing do EI (test set = sessão B) |
| 2 | F1 global do FOMO | ≥ 0,85 | idem (referência do livro: 0,85 treino / 0,83 teste) |
| 3 | Latência de inferência no XIAO | ≤ 200 ms/frame | `result.timing.classification` no Serial |
| 4 | Latência captura → alerta | < 1 s | captura ~100 ms + inferência ~150 ms + confirmação 3 frames ~450 ms + I/O ~50 ms ≈ **750 ms** |
| 5 | Throughput sustentado | ≥ 5 fps | contador no firmware |
| 6 | Falso positivo de alerta | ≤ 1 por ciclo | log do microSD sobre ≥ 10 ciclos, conferido contra vídeo |
| 7 | Falso negativo em desvio grave | 0 em 10 desvios induzidos | testes provocados (pular solda, empilhar antes de montar) |
| 8 | Integridade do log | CSV íntegro por ≥ 1 h | inspeção do cartão |

O critério 6 é sustentado pela **confirmação temporal de N=3 frames** no
firmware: nenhuma leitura vira alerta sem se repetir.

## 5. Rastreabilidade no microSD (sugestão do mentor — implementada)

A cada inferência, uma linha em `/assemblyguard_log.csv`:

```
ms,estado,leitura,pecas_bancada,ferro_em_uso,pilha_entrada,
pilha_bancada,pilha_saida,alvo_x,alvo_y,latencia_ms,alerta
```

E a cada alerta, o JPEG do frame (`/alerta_<ms>.jpg`) — evidência visual do
desvio, com posição registrada.

## 6. Cronograma por marcos (caminho crítico para o demo)

| Marco | Entregável | Status |
|---|---|---|
| M1 | Vídeos inspecionados + 200 frames extraídos (`anotar/`) | **feito** |
| M2 | Dataset anotado no Edge Impulse (3 classes) | ← próximo |
| M3 | FOMO treinado, F1 ≥ 0,85 no test set (sessão B) | |
| M4 | Rodando no XIAO com latência e fps medidos | |
| M5 | Zonas calibradas na bancada + log no SD | |
| M6 | Validação: ≥ 10 ciclos + 10 desvios induzidos, FP/FN contados | |

## 7. Risco nº 1 para o demo: domain shift Hikvision → OV2640

O modelo treina em frames da câmera IP e roda na OV2640 — cor, ângulo,
nitidez e enquadramento diferentes. Mitigação em duas frentes:

1. **Definitiva:** após o M4, capturar ~60–80 fotos com o próprio XIAO na
   bancada, anotar e retreinar juntando os dois domínios.
2. **Rede de segurança da apresentação:** apontar o XIAO para um monitor
   reproduzindo o vídeo C (14 min, vários ciclos). O domínio de teste passa
   a bater com o de treino. Legítimo, desde que declarado no slide.

## 8. O que a V1 continua valendo

- O **classificador MobileNetV2** (notebook) vira *baseline* comparativo no
  relatório: classificação de cena vs. detecção de objetos.
- O **teste de vazamento com rótulos embaralhados** vira seção metodológica.
- Experimento novo possível: rodar o classificador V1 na sessão C (nunca
  vista) e comparar com a acurácia do split por segmento — se despencar, é a
  demonstração empírica do ponto do mentor sobre split por sessão.

---

## Anexo A — Passo a passo no Edge Impulse (M2 → M3)

1. Criar projeto → **Data acquisition → Upload data**:
   `anotar/train/` como *Training*, `anotar/test/` como *Testing*
   (as pastas já respeitam o split por sessão — não usar rebalance).
2. **Labeling queue** (Data acquisition): desenhar caixas de `peca`,
   `pilha`, `ferro_solda`. Ativar *"track objects between frames"* — os
   frames são sequenciais, o rastreio propaga as caixas.
3. **Impulse design**: Image data 96×96, *squash* · bloco Image (Grayscale)
   · bloco **Object Detection (FOMO MobileNetV2 0.35)**.
4. **Treino**: 60 épocas, LR 0.001, batch 32, validação 20 %.
5. **Model testing** → registrar F1 por classe (critérios 1–2).
6. **Deployment → Arduino library** (EON Compiler, int8) → baixar o ZIP.
7. Arduino IDE: *Add .ZIP Library*, abrir `AssemblyGuard_XIAO.ino`, ajustar
   o `#include` para o nome do export, placa **XIAO_ESP32S3**, PSRAM
   **OPI PSRAM**, gravar.
8. Serial Monitor 115200: conferir latência/fps (critérios 3–5) e calibrar
   as zonas com os centroides impressos.
