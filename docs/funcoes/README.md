# Referência das funções

Esta pasta documenta cada função, método e ponto de entrada encontrado no
AssemblyGuard. A referência descreve o comportamento implementado no código
atual; decisões de arquitetura e procedimentos completos continuam nos guias
em [`docs/`](../).

Para uma visão de alto nível antes de entrar nas funções, abra a [árvore
interativa de processos](../arvore-processos.html).

## Python

### Dataset V2

- [`pipeline_deteccao.py`](pipeline-deteccao/README.md): limpeza, amostragem,
  extração e pré-visualização dos frames para detecção FOMO.
- [`gerar_preanotacao.py`](gerar-preanotacao/README.md): geração de caixas
  preliminares por cor e geometria.
- [`inspecionar_videos.py`](inspecionar-videos/README.md): inspeção visual,
  alinhamento e validação do ROI.

### Histórico e validação

- [`pipeline_assemblyguard.py`](pipeline-assemblyguard/README.md): pipeline V1
  de OCR, limpeza e classificação temporal.
- [`assemblyguard_pc.py`](assemblyguard-pc/README.md): inferência FOMO no PC
  usando vídeo, RTSP ou webcam.

## Firmware

- [`AssemblyGuard_XIAO.ino`](firmware/README.md): captura, inferência,
  máquina de estados, persistência no SD e servidor web do XIAO ESP32S3.

Cada índice de módulo lista um arquivo por função. Os nomes dos arquivos usam
`kebab-case` para permanecerem legíveis e compatíveis com diferentes sistemas.
