# `AssemblyGuard_XIAO.ino`

Referência das funções do firmware do XIAO ESP32S3 Sense. A captura usa OV2640
em QVGA/JPEG, a inferência usa a biblioteca exportada do Edge Impulse e o
estado é publicado no Serial, microSD e painel web.

## Zonas e sequência

- [`dentro.md`](dentro.md) · [`proxima.md`](proxima.md) · [`inferir-etapa.md`](inferir-etapa.md)

## Servidor web

- [`web-publica-frame.md`](web-publica-frame.md) · [`handler-index.md`](handler-index.md)
- [`handler-zonas.md`](handler-zonas.md) · [`handler-status.md`](handler-status.md)
- [`handler-stream.md`](handler-stream.md) · [`iniciar-web.md`](iniciar-web.md)
- [`manter-wifi.md`](manter-wifi.md)

## Inicialização e câmera

- [`setup.md`](setup.md) · [`ei-camera-get-data.md`](ei-camera-get-data.md)
- [`jpeg-dims.md`](jpeg-dims.md) · [`capturar.md`](capturar.md) · [`soltar-frame.md`](soltar-frame.md)

## Alertas, logs e execução

- [`salvar-jpeg-alerta.md`](salvar-jpeg-alerta.md) · [`log-csv.md`](log-csv.md)
- [`sinal-alerta.md`](sinal-alerta.md) · [`loop.md`](loop.md)
