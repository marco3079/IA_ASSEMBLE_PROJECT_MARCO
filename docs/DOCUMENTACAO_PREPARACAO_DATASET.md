# AssemblyGuard — Preparação do Dataset

**Projeto:** AssemblyGuard — Detecção de Etapas de Montagem via TinyML (Bloq Volt)
**Curso:** IESTI01 TinyML — CR018-2026_2026_S2_T01
**Responsável por esta etapa:** Carlos Santana (Líder Técnico / ML Engineer)
**Hardware alvo:** Seeed Studio XIAO ESP32S3 Sense (OV2640)

---

## 1. Ponto de partida

O material recebido do time era:

| Arquivo | Conteúdo |
|---|---|
| `WhatsApp_Video_2026-08-21_at_08_29_02.mp4` | 816×464, 30 fps, 243,9 s (7.317 frames) |
| `frames_96x96-...zip` | 300 frames JPEG 96×96, sem rótulo, pasta única |

### 1.1. Problema identificado: vazamento de rótulo (*label leakage*)

A inspeção dos frames revelou que **as imagens não eram recortes de região fixa**,
como se supunha: eram frames inteiros redimensionados, vindos de uma câmera IP
**Hikvision DS-2CD1027G2H-L** com analytics ativo, sobre a qual o time ainda
sobrepôs um texto indicando a etapa.

Elementos gravados nos pixels:

- **`Etapa: <nome>` no canto superior esquerdo** — o rótulo da classe, escrito
  na própria imagem, mudando de cor conforme a etapa;
- painel Hikvision no canto superior direito (`Activity`, `Cycle`, `Abs. Count`,
  `Last Soldering`, `Avg. Gluing`…);
- bounding boxes de analytics desenhadas sobre a cena (`Work table`, `Home zone`,
  `Waiting`, `Glue gun`, `Donuts Stack`);
- tarjas de detecção com score (`Soldering iron 0.65`, `Donuts Stack 0.93`);
- rodapé com o modelo da câmera.

**Por que isso inviabiliza o treino:** o texto `Etapa: X` *é* o rótulo. Uma CNN
aprende o caminho mais curto — leria o texto em vez de olhar o processo. A
métrica de validação bateria perto de 100% e o modelo falharia completamente no
ESP32, onde a OV2640 captura a cena crua, sem overlay nenhum.

O painel e as tarjas de detecção têm o mesmo defeito em grau menor: mudam de
estado conforme a etapa, então também carregam informação de classe.

---

## 2. Solução adotada

Em vez de descartar o material, o overlay foi **transformado em ativo**: ele
serve de fonte de rotulagem automática e depois é apagado da imagem.

```
vídeo original
   │
   ├─► [OCR do texto "Etapa: X"] ──────────► linha do tempo de rótulos
   │
   └─► [remoção dos overlays] ─► [recorte ROI] ─► [resize 96×96] ─► frames limpos
                                                                        │
                        rótulos + frames ─► split por segmento temporal ─┘
```

Implementação: `pipeline_assemblyguard.py`

### 2.1. Rotulagem automática por OCR

O texto muda de **cor** a cada etapa (rosa, azul, amarelo, cinza…), o que quebra
um threshold fixo. A solução foi limiarizar o **canal máximo** da ROI do texto e
testar quatro limiares (110, 150, 90, 170), ficando com a leitura mais longa que
contenha a palavra "etapa".

Sobre a leitura bruta aplicam-se dois filtros:

1. **Normalização por similaridade** contra vocabulário canônico
   (`SequenceMatcher`, corte em 0,65) — corrige `soida`→`solda`,
   `prepare`→`preparo`, `mont3agem`→`montagem`.
   O **sufixo numérico é comparado separadamente e tem de bater exatamente**,
   senão `empilhagem 1` e `empilhagem 2` — que são classes distintas — seriam
   fundidas por similaridade textual. Esse foi um bug real da primeira versão.

2. **Suavização temporal** (mediana de 3): um rótulo isolado entre dois vizinhos
   iguais é erro de OCR, não transição de etapa.

**Resultado:** 476 de 488 leituras válidas — **97,5 %**.

### 2.2. Remoção dos overlays

Três mecanismos combinados:

**(a) Máscara estática automática.** As bounding boxes da Hikvision são
(i) estáticas no tempo, (ii) de cor saturada ou branco puro e (iii) traços
**finos**. Partes estáticas da cena real (chão, caixas) também existem, mas são
blocos **grossos** — então um filtro morfológico de espessura
(`mask − opening(mask, 7×7)`) separa as duas coisas. A máscara é calculada uma
vez a partir de 80 frames amostrados e aplicada com `cv2.inpaint` em todos.

**(b) Detecção dinâmica das tarjas de rótulo.** As tarjas escuras com texto
(`Soldering iron 0.65`) **se movem junto com o objeto detectado**, portanto
correlacionam com a etapa. São localizadas por componentes conexos quase-pretos
com razão largura/altura > 2 e apagadas por inpainting.

**(c) Blocos fixos preenchidos com cinza neutro.** Regiões de posição conhecida
(caixa `Etapa:`, painel Hikvision, rodapé, rótulos estáticos das zonas) são
preenchidas com cinza constante. Cinza constante é idêntico em todos os frames,
logo **não carrega informação de classe** — ao contrário do texto original.

### 2.3. Recorte da área útil (ROI)

Reduzir o frame 816×464 inteiro para 96×96 destruía justamente o detalhe que
distingue as etapas (posição do ferro de solda, componente na mão). O recorte
`(130, 55) → (735, 425)` concentra a resolução onde a ação acontece, antes do
resize.

### 2.4. Split treino/teste por segmento temporal

Conforme orientação do mentor: frames vizinhos de vídeo são quase idênticos, e
split aleatório por frame coloca o mesmo instante nos dois lados, inflando a
acurácia.

Aqui há um único vídeo, então o análogo de "por sessão" é **por segmento**:
frames consecutivos de mesmo rótulo formam um segmento, e **segmentos inteiros**
são alocados a treino ou teste. Foram identificados **14 segmentos válidos**
(mínimo de 30 frames cada).

---

## 3. Dataset resultante

`assemblyguard_dataset_limpo.zip` — 1.428 imagens JPEG 96×96, 6 fps efetivos.

```
dataset/
├── train/  {empilhagem_1, empilhagem_2, espera, fim, montagem, preparo, solda}/
└── test/   {mesmas 7 classes}/
```

| Classe | Treino | Teste | % teste |
|---|---:|---:|---:|
| empilhagem_1 | 183 | 135 | 42 % |
| empilhagem_2 | 80 | 34 | 30 % |
| espera | 128 | 55 | 30 % |
| fim | 38 | 16 | 30 % |
| montagem | 96 | 42 | 30 % |
| preparo | 192 | 15 | 7 % |
| solda | 306 | 108 | 26 % |
| **Total** | **1.023** | **405** | **28 %** |

---

## 4. Limitações — ler antes de interpretar qualquer métrica

1. **Uma única sessão de gravação.** Todo o dataset vem de um vídeo, um
   operador, uma iluminação. Não há como estimar generalização para outro turno
   ou outra pessoa. **Esta é a limitação mais séria do conjunto atual.**

2. **`preparo` com 7 % no teste, `empilhagem_1` com 42 %.** O split aloca
   segmentos inteiros e algumas classes têm poucos segmentos, então a proporção
   alvo de 30 % nem sempre é atingível. Não é ajustável sem quebrar a separação
   temporal — só mais gravações resolvem.

3. **`fim` com 38 exemplos de treino** contra 306 de `solda`. O notebook aplica
   pesos por classe, mas isso compensa parcialmente; a classe seguirá frágil.

4. **Overlays residuais.** As linhas coloridas das bounding boxes não são
   removidas 100 % pelo inpainting. As que sobram são estáticas — idênticas em
   todas as classes, portanto sem informação de rótulo — mas contribuem para
   *domain shift* contra a OV2640.

5. **Domain shift câmera IP → OV2640.** Este é o ponto que mais deve preocupar
   no deploy: cor, ângulo, nitidez e campo de visão da Hikvision são diferentes
   dos da câmera do XIAO. Uma acurácia alta aqui **não** garante desempenho
   embarcado.

6. **Rótulos herdados de terceiros.** As etapas vêm do overlay produzido pelo
   time, não de anotação verificada quadro a quadro. Erros nesse overlay entram
   no dataset como erro de rótulo.

---

## 5. Verificação obrigatória: teste de vazamento

A seção 6 do notebook treina um modelo idêntico com **rótulos embaralhados**.
Se esse modelo de controle acertar bem acima do acaso (1/7 ≈ 14 %), sobrou algum
atalho no dataset e a limpeza precisa ser revista. É a prova de que o modelo
aprendeu o processo, e não um artefato — vale como seção metodológica no
relatório final.

---

## 6. Recomendação que continua valendo

Nada disso substitui **exportar o stream sem OSD/analytics da Hikvision**. Vale
verificar com o responsável pela câmera se o NVR permite gravar o fluxo cru: é
uma configuração, e elimina de uma vez os itens 4 e 6 acima.

Melhor ainda, para o deploy: **regravar parte do dataset com a própria câmera do
XIAO ESP32S3**, na posição definitiva. Resolve o domain shift na raiz.

---

## 7. Arquivos entregues

| Arquivo | Descrição |
|---|---|
| `assemblyguard_dataset_limpo.zip` | Dataset 96×96 pronto para treino |
| `AssemblyGuard_Treinamento.ipynb` | Notebook Colab: treino, avaliação, teste de vazamento, quantização int8 |
| `pipeline_assemblyguard.py` | Script que gerou o dataset (reprodutível) |
| `relatorio_dataset.json` | Segmentos, split e contagens |
| `comparacao_antes_depois.jpg` | Frame original × frame limpo |
| `preview_dataset_por_classe.jpg` | Amostras de cada classe |
| `AssemblyGuard_firmware.ino` | Esqueleto do firmware ESP32S3 (captura + inferência + stream MJPEG) |

---

## 8. Próximos passos sugeridos

1. Rodar o notebook e registrar as métricas por classe.
2. Confrontar com os critérios de sucesso — que ainda **não foram definidos**
   pelo time (apontado pelo mentor): acurácia mínima por classe, latência do
   alerta, taxa de falso positivo aceitável.
3. Executar o teste de vazamento antes de reportar qualquer número.
4. Gravar sessões adicionais — outro operador, outro horário — para as classes
   fracas (`fim`, `empilhagem_2`).
5. Regravar com a câmera do XIAO e refazer o treino antes da validação em campo.

---

## 9. SUPERADO PELA V2 — ler `PLANO_V2_DETECCAO.md`

Após o feedback do mentor (migrar para **detecção de objetos**) e a chegada
de mais dois vídeos, este documento passou a ser histórico. O que mudou:

1. **O vídeo desta V1 ("A") é o vídeo B com o texto `Etapa: X` sobreposto
   pelo time.** Mesma duração e mesmos 7.317 frames, alinhados. Com o B em
   mãos, o vazamento principal (seção 1.1) não existe na fonte — todo o
   esforço de OCR + remoção do texto ficou desnecessário.

2. **Existe uma segunda sessão (vídeo C, 14,4 min, outro operador, outro
   dia) sem overlay nenhum** — sem analytics, sem texto. A limitação nº 1 da
   seção 4 ("uma única sessão") está superada: a V2 treina na sessão C e
   testa na sessão B, split por sessão E por operador.

3. **Correção da seção 4.4:** a afirmação de que os overlays residuais são
   estáticos estava errada — as tarjas de detecção ("Donuts Stack 0.92")
   **se movem com o objeto** e vazam informação. Na V2 elas são removidas
   por *template matching* dos textos (`pipeline_deteccao.py`).

4. **O ROI foi reposicionado.** O recorte da V1 mantinha os três maiores
   overlays dentro do quadro, exigindo os blocos cinza que consumiam ~30 %
   da imagem. O ROI da V2 (frações de `(95, 25, 854, 445)` @ 854×480) deixa
   relógio, painel e rodapé fora ou os apaga por inpainting — sem nenhum
   bloco cinza.

5. **Para detecção de objetos não se rotula etapa** — anotam-se objetos
   (`peca`, `pilha`, `ferro_solda`) no Edge Impulse. O OCR inteiro saiu do
   caminho.

O dataset desta V1 (`assemblyguard_dataset_limpo.zip`) e o notebook
continuam úteis como **baseline comparativo** e a seção 5 (teste de
vazamento) como metodologia. Artefatos da V2: `pipeline_deteccao.py`,
`anotar/` (140 treino + 60 teste), `AssemblyGuard_XIAO.ino`,
`PLANO_V2_DETECCAO.md`.
