# AssemblyGuard — LEIA-ME PRIMEIRO (handoff para outro PC)

**Atualizado em:** 24/08/2026, ~01h
**Projeto:** AssemblyGuard — monitoramento das etapas de montagem do Bloq Volt
com detecção de objetos (FOMO) rodando no **XIAO ESP32S3 Sense**
**Curso:** IESTI01 TinyML (mentor: Prof. Marcelo Rovai)

Este arquivo é o ponto de entrada. Ele resume **tudo que já foi feito, onde
cada coisa está, e o que falta**, para continuar o trabalho em qualquer PC.

---

## 0. O que copiar para o outro PC

**A pasta `Ia_esp32` inteira já contém tudo.** Foi conferido:

- `modelos/` — os modelos treinados exportados do EI (bibliotecas Arduino,
  int8 + EON): `1.0.1` = v1 baseline, `1.0.2` = **v2 atual** (4 classes).
- `videos/` — os 3 vídeos-fonte (73 MB). O vídeo `15.43.32` (sessão C) é
  também a **rede de segurança do demo** (reproduzir num monitor e apontar o
  XIAO para ele).
- `anotar/` — os 200 frames extraídos (140 treino + 60 teste).

O resto do projeto vive **na nuvem do Edge Impulse** e é acessível de
qualquer PC com o login:

| Item | Onde |
|---|---|
| Dataset anotado (200 imgs, 3 classes) | studio.edgeimpulse.com → projeto **AssemblyGuard** (ID **1095021**), conta `Carlosengauto21` |
| Impulse, modelo treinado, builds | mesmo projeto, menu *Impulse design* / *Deployment* |

> ⚠️ No PC novo, os scripts Python apontam para os vídeos em `D:\` (raiz).
> Só importa se for re-extrair frames: ajuste os caminhos no dicionário
> `VIDEOS` de `pipeline_deteccao.py` para a pasta `videos/`.

---

## 1. História do projeto em 60 segundos

1. **V1 (classificação)** — um classificador de cena (7 etapas) foi treinado
   sobre um vídeo de câmera Hikvision. Documentado em
   `DOCUMENTACAO_PREPARACAO_DATASET.md`. O mentor apontou: classificação não
   diz *onde* está o problema → migrar para **detecção de objetos**; a
   restrição de "512 KB" estava errada (o XIAO tem **8 MB de PSRAM + 8 MB
   Flash** além dos 512 KB de SRAM); faltavam critérios de sucesso.
2. **Descobertas nos vídeos** (mudaram tudo):
   - O vídeo usado na V1 ("A", `08.29.02`) é o vídeo **B** (`15.39.00`) com o
     texto "Etapa: X" sobreposto pelo próprio time — o vazamento de rótulo da
     V1 não existe na fonte.
   - O vídeo **C** (`15.43.32`) é uma **segunda sessão**: 14,4 min, outro
     operador, outro dia, **sem overlay nenhum**.
3. **V2 (detecção)** — classes `peca` (donut azul), `pilha` (2+ empilhados),
   `ferro_solda`. O **ciclo do Bloq Volt é delimitado pela pilha**: entra
   pela caixa da direita, termina quando a pilha pronta aparece à esquerda.
   Split **por sessão e por operador**: treino = C, teste = B (o padrão-ouro
   que o mentor pediu). Detalhes em `PLANO_V2_DETECCAO.md`.

---

## 2. O que JÁ está feito (com os porquês)

### 2.1 Dataset (concluído)
- `pipeline_deteccao.py` extraiu **140 frames do C** (treino) e **60 do B**
  (teste), 320×240, em `anotar/`. O B foi limpo por inpainting: máscara
  temporal por mediana (linhas de zona, inclusive sobre a operadora) +
  *template matching* dos textos das tarjas ("Donuts Stack 0.92" etc.).
- Upload feito no Edge Impulse **respeitando o split** (140 Training /
  60 Testing). **Nunca** usar "Perform train/test split" no EI — destruiria a
  separação por sessão.

### 2.2 Anotação (concluída, por IA — revisão humana PENDENTE)
- Usado o **AI labeling do EI (OWL-ViT zero-shot)** em duas passadas:
  1. `blue round plastic piece (peca, 0.15)` + `stack of blue plastic pieces
     (pilha, 0.15)` + `soldering iron (ferro_solda, 0.1)` em todo o dataset;
  2. o ferro saiu com ~6 caixas falsas por imagem → re-rodado **só**
     `soldering iron with cable on a workbench (ferro_solda, 0.22)` com
     *Delete existing bounding boxes = Only if they match any labels in the
     prompt* (apaga só os ferros antigos, preserva peca/pilha).
- Resultado: **1.351 objetos** no treino (867 peca, 371 pilha, 43 ferro).
- **Armadilha conhecida do serviço**: o job falha com `500 Internal Server
  Error` no 1º sample quando a GPU deles está fria. **Solução: re-disparar
  imediatamente** (a falha aquece a GPU). Rodar em "Data without labels"
  preserva o progresso entre tentativas.

### 2.3 Treino (concluído — modelo v1 = baseline)
- Impulse: **96×96, squash, grayscale, FOMO MobileNetV2 0.35**, 60 épocas,
  LR 0.001, augmentation ligado.
- **Validação (int8): F1 0,54** → `pilha` **0,83** / `peca` 0,39 /
  `ferro_solda` 0,27. Precision 0,75, recall 0,42 — ou seja: quando detecta,
  acerta; o problema é **deixar passar**.
- **Model testing na sessão B (número honesto, cross-session): F1 0,23.**
- Causa raiz dos números baixos (diagnóstico, não achismo): anotação 100%
  automática sem revisão → **donuts sem caixa ensinam o modelo que donut é
  fundo** (mata o recall) + só 43 exemplos de ferro.
- On-device: RAM 115 K / Flash 81 K — folga enorme. A latência "743 ms"
  mostrada no EI é para o target errado (ESP-EYE/ESP32 clássico); no S3
  espere **~150 ms**. (Opcional: Dashboard → Target → trocar para o XIAO
  ESP32S3 para as estimativas ficarem realistas.)

### 2.4 Deploy (build feito e baixado)
- **Build v1 "Arduino library"** (int8 + EON) baixado →
  `ei-assemblyguard-arduino-1.0.1.zip` (nesta pasta, 5,3 MB).
- Header interno: `AssemblyGuard_inferencing.h` — **exatamente o include que
  o firmware já usa**, nada a ajustar.

### 2.5 Firmware (pronto para compilar)
`AssemblyGuard_XIAO.ino` — arquivo único com:
- Câmera OV2640 (JPEG QVGA, framebuffer na PSRAM);
- Inferência FOMO → centroides das 3 classes;
- **Mapa de zonas** (frações do frame, constantes no topo — calibrar na
  bancada): entrada à direita, bancada, descanso do ferro, saída à esquerda;
- **Máquina de estados** espera → preparo → montagem → solda → empilhagem →
  fim, com confirmação de 3 frames antes de alerta (segura falso positivo);
- **Log no microSD**: CSV por inferência + JPEG a cada alerta;
- **Servidor web** (pedido do Carlos): o XIAO cria a rede WiFi
  **"AssemblyGuard"** (senha **bloqvolt123**); conectar e abrir
  **http://192.168.4.1** → vídeo ao vivo + painel JSON (estado, peças,
  ciclos, latência) atualizando sozinho. Para usar a rede local em vez de
  AP, preencher `WIFI_STA_SSID/PASS` no topo do arquivo.

---

## 3. O QUE FALTA — na ordem

### Passo 1 — Revisar as anotações (a alavanca de qualidade nº 1)
**Decisão de 25/08:** entra uma 4ª classe, **`aplicador_cola`** (a ferramenta
verde) — anotar na mesma passada de revisão. Guia dedicado para quem for
revisar: `GUIA_ESTAGIARIA.md`.

No Studio: **Data acquisition → clicar num sample → corrigir as caixas →
Save**. Prioridades, do mais ao menos importante:
1. **Donuts visíveis SEM caixa** → adicionar `peca` (é o que mais derruba o
   recall);
2. **`aplicador_cola`** → classe nova, começa com zero caixas: anotar em
   TODO frame em que aparecer (classe nova anotada pela metade se sabota —
   frames com ela visível e sem caixa ensinam o modelo a ignorá-la);
3. **`ferro_solda` faltando** nos frames em que o ferro aparece (só há 43);
4. `peca`×`pilha` trocadas (a pilha delimita o ciclo — o firmware depende);
5. Caixas em lugar nenhum (fantasmas) → deletar.

Por causa da classe nova, a meta passa a ser percorrer as 200 imagens.
Frames B (teste) também contam — o ground truth do teste hoje também é
automático.

**Atualização 28/08 — decisão sobre o ciclo no firmware:** o ciclo real
tem 2 rodadas de solda+cola/montagem e fecha com **2 pilhas** na saída
(bate com `empilhagem_1/2` do overlay da V1). Uma versão fiel chegou a
ser implementada, mas foi **revertida por decisão do Carlos**: para o
demo, vale o ciclo simples `espera → preparo → montagem → solda →
empilhagem → fim` (fecha com 1 pilha na saída) — mais fácil de fechar e
de explicar. O modelo v2 detecta `aplicador_cola`; o firmware imprime a
detecção no Serial mas não a usa na máquina de estados. Refinar o ciclo
para a versão real fica como melhoria pós-demo.

### Passo 2 — Retreinar e reavaliar
Menu **Retrain model** → depois **Model testing → Classify all**. Metas da
tabela de critérios (`PLANO_V2_DETECCAO.md`): F1 ≥ 0,80 por classe, ≥ 0,85
global. Compare com o baseline (0,54 val / 0,23 teste) para mostrar o efeito
da revisão no relatório — isso é resultado, não retrabalho.

### Passo 3 — Rebuild + flash
1. **Deployment → Build** (Arduino library, int8, EON) → baixa o ZIP v2.
   *(No PC do Carlos o Chrome baixa em `D:\`, não em Downloads.)*
2. Arduino IDE 2.x → Boards Manager → **esp32 by Espressif v2.0.17**
   (⚠️ série 3.x quebra os exports do EI). URL de boards:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. **Sketch → Include Library → Add .ZIP Library** → o ZIP (v1 desta pasta
   serve para o primeiro teste; v2 quando existir).
4. Abrir `AssemblyGuard_XIAO.ino` → Board **XIAO_ESP32S3** →
   **PSRAM: OPI PSRAM** (⚠️ sem isso a câmera não sobe) → Upload
   (se falhar: segurar BOOT ao plugar o USB).
5. Serial Monitor 115200 → anotar `lat=...ms` e fps (critérios 3 e 5).
6. microSD **FAT32 ≤ 32 GB** no slot. ⚠️ LED e SD compartilham o GPIO21
   (já tratado no firmware).

### Passo 4 — Demo de mesa (sem fábrica)
Apontar o XIAO para um monitor reproduzindo `videos/WhatsApp Video
2026-08-12 at 15.43.32.mp4` (sessão C) em tela cheia. Conectar na rede
"AssemblyGuard" → http://192.168.4.1 → ajustar as **zonas** no topo do `.ino`
com os centroides impressos, regravar, até sair `== CICLO 1 COMPLETO`.
Legítimo para a apresentação, desde que declarado no slide.

### Passo 5 — Validação de verdade (critérios 6–8)
≥ 10 ciclos + 10 desvios induzidos (pular solda, empilhar antes da hora),
contando FP/FN pelo CSV do microSD. Tabela completa de critérios e marcos:
`PLANO_V2_DETECCAO.md` (o projeto está no marco **M4 parcial** — modelo
baixado, falta rodar no hardware).

### Melhorias conhecidas (depois do demo funcionar)
- Capturar 60–80 fotos **com o próprio XIAO** na bancada, anotar e
  retreinar juntando — resolve o domain shift Hikvision→OV2640 na raiz.
- Trocar o target do EI para XIAO ESP32S3 (estimativas realistas).
- Rodar o classificador V1 na sessão C e comparar — demonstração empírica
  do ponto do mentor sobre split (seção 8 do PLANO_V2).

---

## 4. Mapa da pasta (reorganizada em 28/08 p/ compartilhar)

| Arquivo/pasta | O que é |
|---|---|
| `README.md` | apresentação do projeto (porta de entrada p/ grupo e professor) |
| `LEIA-ME_PRIMEIRO.md` | **este arquivo** — handoff detalhado |
| `docs/PLANO_V2_DETECCAO.md` | resposta ponto a ponto ao mentor: arquitetura V2, critérios de sucesso, marcos |
| `docs/GUIA_EDGE_IMPULSE.md` | passo a passo detalhado do Studio (upload → anotação → treino → deploy → flash) |
| `docs/GUIA_ESTAGIARIA.md` (+ `.html`) | guia de anotação/retreino/export para quem revisa o dataset |
| `docs/DOCUMENTACAO_PREPARACAO_DATASET.md` | histórico da V1 (classificação) — seção 9 marca o que foi superado |
| `AssemblyGuard_XIAO/AssemblyGuard_XIAO.ino` | **firmware** (FOMO + zonas + estados + SD + painel web com overlay) |
| `modelos/ei-assemblyguard-arduino-1.0.1.zip` | modelo v1 (baseline) — biblioteca Arduino p/ Add .ZIP Library |
| `modelos/ei-assemblyguard-arduino-1.0.2-impulse_1.zip` | **modelo v2** (4 classes, pós-revisão) — o atual |
| `anotar/` | 200 frames extraídos (train/ = sessão C, test/ = sessão B) + previews |
| `videos/` | os 3 vídeos-fonte (B = A sem overlay de texto; C = sessão limpa) |
| `scripts/pipeline_deteccao.py` | extração/limpeza dos frames da V2 (ROI, inpainting, template matching) |
| `scripts/pipeline_assemblyguard.py` | pipeline da V1 (OCR + limpeza) — mantido como histórico e por reuso |
| `scripts/inspecionar_videos.py` | folhas de contato / comparativos que revelaram A≡B e a sessão C |
| `scripts/gerar_preanotacao.py` | pré-anotação local por cor — **descartada**; mantida só como registro |
| `scripts/assemblyguard_pc.py` | inferência no PC (Hikvision RTSP / vídeo) com o `.tflite` — rascunho |
| `historico_v1/` | notebook, dataset limpo e relatório da V1 — *baseline comparativo* do relatório |
| `evidencias/` | folhas de contato, ROIs e comparativos usados nas decisões |

## 5. Requisitos no PC novo

- **Nada** para mexer no Edge Impulse (é web) e no firmware (Arduino IDE).
- Arduino IDE 2.x + core esp32 **2.0.17** (passo 3 acima).
- Python 3.11 + `pip install opencv-python` **apenas** se for re-extrair
  frames dos vídeos (não é necessário para o fluxo normal).
