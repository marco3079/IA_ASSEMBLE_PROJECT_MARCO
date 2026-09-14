# `handler_stream`

Entrega o JPEG mais recente como MJPEG na porta 81. Usa um buffer temporário e
libera o mutex antes do envio para não bloquear os demais endpoints.
