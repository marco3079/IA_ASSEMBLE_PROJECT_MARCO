# Guia da Estagiária — AssemblyGuard no Edge Impulse

**Projeto:** AssemblyGuard — monitoramento das etapas de montagem do Bloq Volt
**Ferramenta:** Edge Impulse Studio (tudo pelo navegador, nada para instalar)
**Tempo estimado:** ~meio dia, sendo a revisão das anotações o grosso do trabalho

Este guia foi escrito para quem nunca usou o Edge Impulse. Siga na ordem.
Qualquer dúvida que não estiver aqui, anote e pergunte — não "chute" em
botões que mudam o dataset.

---

## 1. Entenda o projeto antes de clicar em qualquer coisa

Uma câmera filma a bancada onde o operador monta o **Bloq Volt** (as peças
azuis em formato de "donut"). Um pequeno computador (XIAO ESP32S3) roda um
modelo de visão computacional que **detecta objetos** no vídeo. O modelo
NÃO decide a etapa do processo — ele só responde: *"onde estão as peças, as
pilhas e o ferro de solda neste frame?"*. Quem deduz a etapa é o programa
do equipamento, cruzando a **posição** desses objetos com zonas da imagem.

Por isso o seu trabalho de anotação é tão importante: se o modelo errar
"onde estão os objetos", toda a lógica de etapas erra junto.

### 1.1 As 4 classes de objeto (o que recebe caixa)

| Classe | O que é | Exemplos |
|---|---|---|
| `peca` | **UM** donut azul sozinho | na bancada, na mão do operador, na bandeja |
| `pilha` | **2 ou mais** donuts empilhados | colunas do estoque na caixa de entrada, pilha em formação na bancada, pilha pronta na saída |
| `ferro_solda` | o corpo do ferro de solda | no suporte de descanso ou na mão |
| `aplicador_cola` | o aplicador de cola (ferramenta de corpo verde) | na mão do operador ou apoiado na bancada |

**Como diferenciar `peca` de `pilha`:** conte. 1 donut = `peca`.
2 ou mais empilhados = `pilha`. Se estão lado a lado (não empilhados),
cada um é uma `peca` separada.

**Atenção com `aplicador_cola`:** é uma classe NOVA — a anotação automática
não a conhece, então ela começa com ZERO caixas. Você vai criá-las todas.
Ele é pequeno e fica muito na mão do operador; vale a mesma regra da mão
coberta (item 3 das regras de ouro).

### 1.2 As etapas do processo (o que o equipamento deduz)

A imagem da câmera é dividida em 4 zonas:

```
 ┌─────────────────────────────────────────────┐
 │  SAÍDA          BANCADA           ENTRADA   │
 │ (esquerda)    (centro - área      (direita  │
 │  pilha         de trabalho)        caixa de │
 │  pronta                            origem)  │
 │            DESCANSO DO FERRO                │
 │            (canto inferior esq.)            │
 └─────────────────────────────────────────────┘
```

E a sequência esperada de um ciclo é:

| # | Etapa | O que caracteriza (regra) |
|---|---|---|
| 0 | **espera** | nenhum objeto relevante na bancada |
| 1 | **preparo** | ≥ 1 `peca` na bancada |
| 2 | **montagem** | ≥ 3 `peca` na bancada |
| 3 | **solda** | `ferro_solda` FORA da zona de descanso (está em uso) |
| 4 | **empilhagem** | `pilha` presente na bancada |
| 5 | **fim** | `pilha` presente na zona de SAÍDA |

- **INÍCIO do ciclo:** a `pilha` de peças **entra pela zona da direita**
  (a caixa de origem, o estoque). É dali que o operador pega o material.
- **FIM do ciclo:** o ciclo termina quando a **pilha pronta aparece na
  zona de saída, à esquerda**. É a `pilha` que delimita o ciclo — por
  isso ela é a classe mais crítica de todas.

Você não precisa configurar nada disso no Edge Impulse — as etapas vivem
no firmware. Mas precisa saber disso para anotar direito: uma `pilha`
confundida com `peca` quebra a detecção de início/fim do ciclo.

> A etapa de **cola** ainda não está na tabela: ela será acrescentada ao
> programa do equipamento depois que o modelo estiver detectando o
> `aplicador_cola`. A anotação vem primeiro — é o seu trabalho que
> habilita essa etapa.

---

## 2. Acessar o projeto

1. Abra **https://studio.edgeimpulse.com** no Chrome.
2. Faça login com a conta **`Carlosengauto21`** (peça a senha ao Carlos).
3. Abra o projeto **AssemblyGuard** (ID **1095021**).

O projeto JÁ tem as 200 imagens carregadas e uma anotação automática
feita por IA. **O seu trabalho principal é a Parte B (revisar/corrigir).**
A Parte A (importar) só é necessária se for começar um projeto do zero ou
adicionar imagens novas.

---

## Parte A — Importar as imagens (só se necessário)

> Pule para a Parte B se estiver usando o projeto AssemblyGuard existente,
> que já tem as 200 imagens.

As imagens ficam na pasta `Ia_esp32\anotar\` do computador:
`train\` (140 fotos da sessão C) e `test\` (60 fotos da sessão B).
Essa separação é PROPOSITAL: treino e teste vêm de **dias e operadores
diferentes**, para provar que o modelo generaliza.

1. Se for projeto novo: **Create new project** → nome → em tipo de
   projeto, escolha **Images → Classify multiple objects (object
   detection)**. Sem isso não existe fila de anotação (erro mais comum).
2. **Data acquisition → Upload data**.
3. **1º lote:** selecione os **140 JPG de `anotar\train`** →
   em *Upload into category* marque **Training** → em label, deixe
   **Unlabeled** (o rótulo virá das caixas desenhadas, não do nome do
   arquivo) → **Upload**.
4. **2º lote:** os **60 JPG de `anotar\test`** → categoria **Testing** →
   Unlabeled → **Upload**.
5. Confira no topo do Data acquisition: deve mostrar **140 / 60
   (70% / 30%)**.

> ⚠️ **NUNCA clique em "Perform train/test split".** Esse botão embaralha
> as imagens entre treino e teste e destrói a separação por sessão — não
> tem desfazer. Se clicou sem querer, pare e avise o Carlos.

---

## Parte B — Revisar e corrigir as anotações (o trabalho principal)

A anotação atual foi feita por uma IA, sem revisão humana. Ela erra de um
jeito conhecido: **deixa objetos sem caixa**. E isso é o pior erro
possível, porque uma peça visível sem caixa ensina o modelo que "aquilo é
fundo" — o modelo aprende a IGNORAR a peça.

### Regras de ouro da anotação

1. **TODO objeto visível das 4 classes precisa de caixa, em TODAS as
   imagens.** Inclusive as colunas de donuts do estoque (caixa da
   direita) e os donuts da bandeja. Parece exagero, mas é assim que o
   modelo funciona.
2. **A caixa deve estar CENTRADA no objeto.** O modelo (FOMO) usa só o
   centro da caixa; o tamanho exato importa menos que o centro estar em
   cima do objeto.
3. **Objeto parcialmente coberto pela mão:** se ainda dá para reconhecer,
   anote a parte visível. Se está mais de ~70% coberto, não anote.
4. **Consistência vale mais que perfeição:** a mesma coisa recebe o mesmo
   rótulo em todas as imagens.
5. Nos frames `B_*` (teste) podem aparecer pequenos borrões da limpeza
   digital do vídeo — ignore os borrões, anote os objetos.

### O passo a passo

1. **Data acquisition** → clique numa imagem da lista. Abre o editor de
   caixas.
2. Corrija na seguinte ordem de prioridade (do que mais melhora o modelo
   para o que menos melhora):
   - **1º — Donuts visíveis SEM caixa** → desenhe uma caixa e rotule
     `peca` (ou `pilha` se forem 2+ empilhados). É o erro mais comum e o
     que mais derruba o desempenho.
   - **2º — `aplicador_cola`** (a ferramenta verde) → classe nova, sem
     nenhuma caixa ainda. Anote em **todo frame em que ele estiver
     visível** — na mão ou apoiado na bancada. Na primeira caixa, digite
     o nome exatamente assim: `aplicador_cola`.
   - **3º — `ferro_solda` sem caixa** nos frames em que o ferro aparece
     (hoje só existem 43 exemplos — pouquíssimo).
   - **4º — `peca` e `pilha` trocadas** → corrija o rótulo (clique na
     caixa e mude a classe). Lembre: a `pilha` delimita o ciclo.
   - **5º — Caixas "fantasma"** (caixa onde não há nada) → delete.
3. Clique **Save labels** e vá para a próxima imagem.
4. Para desenhar uma caixa: clique e arraste com o mouse. Para rotular:
   digite ou escolha `peca`, `pilha`, `ferro_solda` ou `aplicador_cola`
   no dropdown.
5. **Revise TAMBÉM os frames `B_*` (Testing)** — eles são o gabarito da
   prova. Se o gabarito está errado, a nota do modelo não significa nada.

**Quanto revisar?** Por causa do `aplicador_cola`, a meta passa a ser
**passar por TODAS as 200 imagens** — uma classe nova anotada só em parte
do dataset se sabota sozinha (os frames onde ela aparece sem caixa
ensinam o modelo a ignorá-la). A boa notícia: a câmera é fixa e as cenas
se repetem; depois das primeiras imagens flui a ~15–20 segundos por
imagem. Se o tempo apertar, priorize completar o `aplicador_cola` e os
donuts sem caixa em todas as imagens, e deixe os retoques finos por
último.

---

## Parte C — Retreinar o modelo

Depois de revisar as anotações:

1. Menu lateral → **Object detection** (dentro de Impulse design).
2. Confira os parâmetros (já devem estar assim; se não, ajuste):
   - Training cycles (épocas): **60**
   - Learning rate: **0.001**
   - Validation set size: **20%**
   - Data augmentation: **ligado**
   - Arquitetura: **FOMO (Faster Objects, More Objects) MobileNetV2 0.35**
3. Clique **Save & train** (ou **Start training**). Demora ~10–15 min.
   Pode deixar a aba aberta e fazer outra coisa.
4. Ao terminar, anote o **F1 score** de cada classe que aparece na tela
   (validação). Para comparar: antes da sua revisão, os números eram
   F1 geral **0,54** — `pilha` 0,83, `peca` 0,39, `ferro_solda` 0,27.
   O `aplicador_cola` não tem número anterior (classe nova — o primeiro
   F1 dele é obra sua). Qualquer melhora sobre o baseline é resultado
   SEU — registre para o relatório.

> Se a página pedir para regenerar features antes do treino: aba
> **Image** → **Save parameters** → **Generate features** → espere →
> volte ao Object detection.

---

## Parte D — Avaliar no conjunto de teste (o número que vale)

O F1 do treino usa imagens "parecidas" com as de treino. O número honesto
vem do teste, que é outra sessão, outro dia, outra pessoa operando:

1. Menu **Model testing** → **Classify all**.
2. Espere terminar e anote o **F1 por classe** e o global.
3. **Metas do projeto:** F1 ≥ **0,80** por classe e ≥ **0,85** global.
   Baseline anterior (sem revisão humana): **0,23**. Não desanime se não
   bater a meta de primeira — compare com 0,23 e registre a evolução.
4. Se `peca` e `pilha` estiverem se confundindo sistematicamente na
   matriz de confusão, anote e avise o Carlos (existe um plano B).

---

## Parte E — Exportar o modelo treinado

O modelo sai do Edge Impulse como uma "biblioteca Arduino" (um arquivo
.zip) que depois é gravada no equipamento:

1. Menu **Deployment**.
2. No campo de busca de formato, escolha **Arduino library**.
3. Em *Model optimizations*: selecione **Quantized (int8)**.
4. **EON Compiler: ligado** (deixa o modelo menor e mais rápido).
5. Clique **Build**. O navegador baixa um arquivo
   `ei-assemblyguard-arduino-X.Y.Z.zip`.
   *(No PC do Carlos o Chrome baixa em `D:\`, não em Downloads.)*
6. **Entregue esse .zip** — copie para a pasta do projeto
   (`Ia_esp32\`) com o nome original e avise que a versão nova está
   pronta. NÃO precisa descompactar.

Quem grava no equipamento usa esse zip no Arduino IDE
(passo a passo em `GUIA_EDGE_IMPULSE.md`, Fase 6).

---

## Checklist final

- [ ] Todas as 200 imagens percorridas (treino E teste)
- [ ] Nenhum donut visível ficou sem caixa nas imagens revisadas
- [ ] `aplicador_cola` anotado em TODOS os frames em que aparece (classe nova, começou do zero)
- [ ] `pilha` vs `peca` conferidos (2+ empilhados = pilha)
- [ ] Modelo retreinado (60 épocas, FOMO 0.35)
- [ ] F1 de validação anotado e comparado com o baseline 0,54
- [ ] Model testing rodado; F1 do teste anotado e comparado com 0,23
- [ ] ZIP exportado (int8 + EON) e copiado para a pasta do projeto
- [ ] Botão "Perform train/test split" **não** foi clicado em momento algum

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Não aparece opção de desenhar caixas | O projeto não está em modo *Bounding boxes*: Dashboard → Project info → Labeling method → **Bounding boxes (object detection)** |
| O treino termina com erro | Rode **Generate features** na aba Image e treine de novo |
| "Job failed / 500" em ferramentas de IA do Studio | Bug conhecido do servidor deles: **rode de novo imediatamente**, costuma funcionar na 2ª tentativa |
| F1 do teste bem menor que o da validação | Esperado até certo ponto (o teste é outra sessão). Se a diferença for enorme, chame o Carlos com a matriz de confusão |
| Cliquei em algo e o dataset mudou | Pare, não tente consertar sozinha, avise o Carlos |
