# Guia passo a passo — do upload ao XIAO rodando (M2 → M4)

Companheiro do `PLANO_V2_DETECCAO.md`. Siga na ordem; os tempos estimados
estão em cada fase. Total: ~meio dia de trabalho, sendo a anotação o grosso.

---

## Fase 0 — Conta e projeto (10 min)

1. Acesse **https://studio.edgeimpulse.com** e crie uma conta gratuita
   (o plano free comporta este projeto com folga).
2. **Create new project** → nome `AssemblyGuard` → Create.
3. Se aparecer um assistente perguntando o tipo de dado/projeto, escolha
   **Images** → **Classify multiple objects (object detection)**.
   Se não aparecer: vá em **Dashboard**, role até **Project info** e mude
   **Labeling method** para **Bounding boxes (object detection)**.
   ⚠️ Sem isso a fila de anotação não existe — é o erro mais comum.
4. Ainda no Dashboard, em **Target device**, selecione o XIAO ESP32S3 se
   estiver na lista (senão, `Espressif ESP-EYE (ESP32)`). Isso só afeta as
   estimativas de latência mostradas — não o modelo.

## Fase 1 — Upload com o split certo (10 min)

O split por sessão JÁ está feito nas pastas. O upload só precisa respeitá-lo:

1. **Data acquisition** → botão **Upload data**.
2. Primeiro lote: **Select files** → selecione os **140 JPG de
   `D:\Ia_esp32\anotar\train`** → em *Upload into category* marque
   **Training** → se aparecer campo de label, marque **Unlabeled**
   (o rótulo virá das caixas, não do nome do arquivo) → Upload.
3. Segundo lote: os **60 JPG de `anotar\test`** → categoria **Testing** →
   Unlabeled → Upload.
4. Confira no topo do Data acquisition: deve mostrar exatamente
   **70% / 30% (140 / 60)**. Não use o botão "Perform train/test split"
   NUNCA — ele embaralharia e destruiria o split por sessão.

## Fase 2 — Anotação (1,5–2,5 h; o gargalo)

**Regras de anotação — leia antes de desenhar a primeira caixa:**

- **`peca`** = um donut azul individual (na bancada, na mão, na bandeja).
- **`pilha`** = 2+ donuts empilhados: as colunas do estoque na caixa de
  entrada, a pilha em formação na bancada e a pilha pronta na saída.
- **`ferro_solda`** = o corpo do ferro de solda (no suporte ou na mão).
- **Regra de ouro do FOMO: TODO objeto visível dessas classes precisa de
  caixa, em TODAS as imagens.** Uma peça visível sem caixa ensina o modelo
  que "aquilo é fundo" — é pior que não ter a imagem. Sim, isso inclui as
  colunas do estoque na caixa de origem e os donuts da bandeja da direita.
  Parece muito, mas a câmera é fixa: o rastreamento propaga essas caixas.
- Caixa **centrada** no objeto: o FOMO usa só o centro; o tamanho exato
  importa menos que o centro estar em cima do objeto.
- Objeto tapado pela mão: se ainda dá pra reconhecer, anote a parte
  visível; se está >70% coberto, não anote.
- Consistência > perfeição: a mesma coisa recebe o mesmo rótulo em todas
  as imagens.

**O trabalho em si:**

1. **Data acquisition → aba Labeling queue** (mostra 200 itens).
2. No canto da tela de anotação, em **Label suggestions**, escolha
   **"Track objects between frames"**.
3. Primeira imagem: desenhe cada caixa com o mouse; digite o nome da
   classe na primeira vez (`peca`, `pilha`, `ferro_solda`) — depois vira
   dropdown. Clique **Save labels** para avançar.
4. Da segunda em diante, as caixas vêm propagadas: **confira, corrija o
   que moveu, adicione o que apareceu, delete o que sumiu**, Save.
5. As primeiras ~15 imagens são lentas (montando o cenário); depois flui
   a ~15 s por imagem.
6. Faça primeiro os frames `C_*` (treino, sem artefato), depois os `B_*`.
   Nos frames B, ignore borrões/restos de limpeza — anote os objetos.

## Fase 3 — Impulse (5 min de cliques + ~15 min de treino)

1. **Create impulse**: Image data → width **96**, height **96**, resize
   mode **Squash** → Add processing block **Image** → Add learning block
   **Object Detection (Images)** → **Save impulse**.
2. Aba **Image**: Color depth **Grayscale** → Save parameters →
   **Generate features** (espere concluir).
3. Aba **Object detection**:
   - Training cycles: **60**
   - Learning rate: **0.001**
   - Validation set size: **20%**
   - Data augmentation: **ligado**
   - Em *Neural network architecture* → Choose a different model →
     **FOMO (Faster Objects, More Objects) MobileNetV2 0.35**
   - **Start training**.
4. Ao final, olhe o **F1 score** por classe na validação e a matriz de
   confusão. Referência do livro: F1 ~0,85.

## Fase 4 — Model testing (5 min) — o número que vale

1. Aba **Model testing** → **Classify all**.
2. Isso roda o modelo na **sessão B inteira** (operadora que o modelo
   nunca viu). O F1 daqui é o que vai na tabela de critérios do
   `PLANO_V2_DETECCAO.md` (metas: ≥0,80 por classe, ≥0,85 global).
3. Se `peca`×`pilha` se confundirem (troca sistemática na matriz),
   registre e me avise — plano B é fundir as duas e usar contagem.

## Fase 5 — Exportar a biblioteca Arduino (5 min)

1. **Deployment** → busque **Arduino library**.
2. Model optimizations: **Quantized (int8)** · EON Compiler: **ligado**.
3. Confira as estimativas exibidas (RAM ~250 KB, latência <200 ms no
   target ESP32) → **Build** → baixa `ei-assemblyguard-arduino-*.zip`.

## Fase 6 — Arduino IDE e flash (30 min na primeira vez)

1. Instale o **Arduino IDE 2.x**.
2. **File → Preferences → Additional boards manager URLs**, cole:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
3. **Boards Manager** → instale **esp32 by Espressif Systems**, versão
   **2.0.17** (⚠️ evite a série 3.x — quebra os exports do Edge Impulse).
4. **Sketch → Include Library → Add .ZIP Library** → o ZIP da Fase 5.
5. Abra `D:\Ia_esp32\AssemblyGuard_XIAO.ino` e ajuste a linha do include
   para o nome real do header do seu export (abra o ZIP e veja:
   `<algo>_inferencing.h`).
6. **Tools**:
   - Board: **XIAO_ESP32S3** (em "ESP32 Arduino")
   - **PSRAM: OPI PSRAM** ⚠️ (sem isso a câmera não sobe)
   - Port: a COM que aparecer ao plugar o USB-C
7. **Upload**. Se falhar a conexão: segure o botão **BOOT** do XIAO
   enquanto pluga o cabo, solte, e tente de novo.
8. **Serial Monitor @ 115200**: deve listar os centroides detectados,
   `lat=...ms` e o estado. Anote latência e fps (critérios 3 e 5).
9. microSD: cartão **≤32 GB formatado FAT32** no slot da placa Sense.
   O log nasce em `/assemblyguard_log.csv`.

## Fase 7 — Primeiro teste sem ir à fábrica (o demo de mesa)

Aponte o XIAO para um **monitor reproduzindo o vídeo C em tela cheia**:
o domínio bate com o do treino e valida o pipeline inteiro (detecção →
zonas → estados → alerta → SD) na sua mesa. Ajuste as zonas no topo do
`.ino` com base nos centroides impressos no Serial e regrave até o ciclo
completar (`== CICLO 1 COMPLETO`). Este teste JÁ é material de
apresentação — declarado como reprodução de vídeo no slide.

---

### Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Não existe "Labeling queue" | Labeling method não está em *Bounding boxes* (Fase 0.3) |
| Upload foi tudo pra Training | Esqueceu de trocar a categoria no 2º lote |
| "ERRO: camera nao inicializou" | PSRAM não está em **OPI PSRAM**, ou flat cable da câmera solto |
| Compila mas Serial vazio | Trocar cabo USB (tem cabo só-carga); conferir 115200 |
| F1 alto no treino, baixo no teste | Esperado até certo ponto (outra sessão); se despencar, me chame com a matriz |
| SD não monta | Cartão >32 GB ou não-FAT32 |
