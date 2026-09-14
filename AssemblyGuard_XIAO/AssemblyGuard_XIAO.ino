/*
 * AssemblyGuard V2 - XIAO ESP32S3 Sense
 * =====================================
 * Deteccao de objetos (FOMO) + mapa de zonas + maquina de estados do
 * ciclo do Bloq Volt + log de rastreabilidade no microSD.
 *
 * Fluxo:
 *   OV2640 -> FOMO 96x96 (centroides de peca / pilha / ferro_solda)
 *          -> em que ZONA esta cada centroide
 *          -> maquina de estados da sequencia esperada
 *          -> alerta (Serial + LED) com a POSICAO do desvio
 *          -> linha de CSV no microSD a cada inferencia
 *
 * Ciclo do Bloq Volt (confirmado com o time):
 *   a pilha de pecas ENTRA pela zona da direita (caixa de origem) e o
 *   ciclo termina quando a pilha pronta aparece na zona de SAIDA, a
 *   esquerda da bancada. espera -> preparo -> montagem -> solda ->
 *   empilhagem -> fim.
 *
 * Antes de compilar:
 *   1. Treine o FOMO no Edge Impulse e exporte "Arduino library"
 *      (Deployment -> Arduino library, com EON Compiler + int8).
 *   2. Sketch -> Include Library -> Add .ZIP Library com o export.
 *   3. Ajuste o include abaixo para o nome do SEU projeto no EI.
 *   4. Tools -> Board: "XIAO_ESP32S3"; Tools -> PSRAM: "OPI PSRAM".
 *      O framebuffer da camera vai na PSRAM de 8 MB - e por isso que a
 *      "restricao de 512 KB" do documento V1 estava errada: os 512 KB
 *      sao so a SRAM interna.
 *
 * Hardware (XIAO ESP32S3 Sense):
 *   - ESP32-S3R8: 512 KB SRAM + 8 MB PSRAM + 8 MB Flash
 *   - OV2640 no conector da placa Sense
 *   - microSD ate 32 GB, FAT32
 *   - ATENCAO: o LED onboard e o CS do microSD compartilham o GPIO21.
 *     Com o log em SD ligado o LED fica em modo "pisca so no alerta"
 *     (o pisca inverte o CS por alguns ms com o barramento ocioso, o
 *     que e tolerado; se o seu cartao nao tolerar, desligue USA_LED).
 */

#include <AssemblyGuard_inferencing.h>   // <-- nome do export do SEU projeto EI
#include "edge-impulse-sdk/dsp/image/image.hpp"
#include "esp_camera.h"
#include "esp_http_server.h"
#include "FS.h"
#include "SD.h"
#include "SPI.h"
#include <WiFi.h>

// ---------------------------------------------------------------------
// CONFIG GERAL
// ---------------------------------------------------------------------
#define USA_SD   1        // 1 = log CSV + JPEG de alerta no microSD
#define USA_LED  1        // LED onboard (GPIO21, compartilhado com SD CS)
#define USA_WEB  1        // 1 = servidor web com stream da camera

// Orientacao da camera (conferir no stream: operador no TOPO, estoque/
// entrada a DIREITA). Camera montada de cabeca p/ baixo: ponha os DOIS em 1
// (girar 180 = vflip + hmirror). Imagem so espelhada: apenas CAM_HMIRROR 1.
#define CAM_VFLIP   0     // 1 = vira verticalmente
#define CAM_HMIRROR 0     // 1 = espelha horizontalmente

#define PIN_SD_CS   21
#define PIN_LED     21    // mesmo GPIO; ver nota acima

// ---- WiFi do monitor -------------------------------------------------
// Modo padrao: o XIAO cria a PROPRIA rede (nao depende do WiFi da
// fabrica). Conecte o celular/notebook nela e abra http://192.168.4.1
// Para usar a rede local em vez disso, preencha WIFI_STA_SSID.
#define WIFI_AP_SSID  "AssemblyGuard"
#define WIFI_AP_PASS  "bloqvolt123"     // minimo 8 caracteres
#define WIFI_STA_SSID ""                // vazio = modo AP
#define WIFI_STA_PASS ""

// confirmacao temporal: uma leitura so vira mudanca de estado/alerta
// depois de N_CONFIRMA frames consecutivos iguais. E o que segura a
// taxa de falso positivo (criterio: <= 1 alerta falso por ciclo).
#define N_CONFIRMA  3

#define CONF_MIN    0.40f // confianca minima (BAIXADO p/ calibracao; voltar a 0.60 depois)

// ---------------------------------------------------------------------
// CAMERA - pinos do XIAO ESP32S3 Sense (CAMERA_MODEL_XIAO_ESP32S3)
// ---------------------------------------------------------------------
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

#define CAM_W 320
#define CAM_H 240

// ---------------------------------------------------------------------
// ZONAS - em fracao do frame de inferencia (0.0-1.0), medidas na CENA
// vista pela propria camera do XIAO. CALIBRAR NA BANCADA: aponte a
// camera, olhe o Serial (imprime os centroides) e ajuste os retangulos.
// Layout confirmado nos videos: entrada a DIREITA, saida a ESQUERDA.
// ---------------------------------------------------------------------
struct Zona { float x1, y1, x2, y2; const char* nome; };

Zona ZONA_ENTRADA  = {0.72f, 0.05f, 1.00f, 0.60f, "entrada"};        // caixa origem (dir.)
Zona ZONA_BANCADA  = {0.25f, 0.25f, 0.72f, 0.80f, "bancada"};        // area de trabalho
Zona ZONA_FERRO    = {0.05f, 0.55f, 0.25f, 0.90f, "descanso_ferro"}; // suporte do ferro
Zona ZONA_SAIDA    = {0.00f, 0.15f, 0.20f, 0.55f, "saida"};          // pilha pronta (esq.)

// ---------------------------------------------------------------------
// MAQUINA DE ESTADOS DO CICLO
// ---------------------------------------------------------------------
enum Etapa { ESPERA, PREPARO, MONTAGEM, SOLDA, EMPILHAGEM, FIM, N_ETAPAS };
const char* NOME_ETAPA[N_ETAPAS] =
    {"espera", "preparo", "montagem", "solda", "empilhagem", "fim"};

Etapa  estado          = ESPERA;
Etapa  leitura_ant     = ESPERA;
int    frames_iguais   = 0;
int    ciclos_ok       = 0;
uint32_t t_inicio_ciclo = 0;

// ---------------------------------------------------------------------
// ESTADO POR FRAME (preenchido a cada inferencia)
// ---------------------------------------------------------------------
struct Leitura {
    int   pecas_bancada;
    bool  ferro_em_uso;       // ferro detectado FORA do descanso
    bool  pilha_entrada;
    bool  pilha_bancada;
    bool  pilha_saida;
    float alvo_x, alvo_y;     // centroide mais relevante (para o alerta)
};

static bool dentro(const Zona& z, float x, float y) {
    return x >= z.x1 && x <= z.x2 && y >= z.y1 && y <= z.y2;
}

// sequencia esperada; de FIM volta a ESPERA (novo ciclo)
// (definida depois dos structs: o gerador de prototipos do Arduino IDE
//  insere os prototipos antes da 1a funcao do arquivo, e tudo que as
//  funcoes recebem por parametro precisa estar declarado antes disso)
Etapa proxima(Etapa e) { return (e == FIM) ? ESPERA : (Etapa)(e + 1); }

// ---------------------------------------------------------------------
// BUFFERS
// ---------------------------------------------------------------------
static uint8_t* rgb888 = nullptr;   // CAM_W*CAM_H*3, alocado na PSRAM
static camera_fb_t* fb_atual = nullptr;

// ---------------------------------------------------------------------
// SERVIDOR WEB (stream MJPEG + status das deteccoes)
// ---------------------------------------------------------------------
#if USA_WEB
// O loop de inferencia roda no core do Arduino; o servidor roda nas
// tasks do esp_http_server. A troca entre eles e feita por um buffer
// duplo do ultimo JPEG + um snapshot do status, ambos sob mutex.
static SemaphoreHandle_t web_mutex;
static uint8_t* web_jpeg = nullptr;       // copia do ultimo frame (PSRAM)
static size_t   web_jpeg_len = 0;
static char     web_status[512] = "{}";   // JSON pronto para servir

static void web_publica_frame(const uint8_t* buf, size_t len) {
    if (xSemaphoreTake(web_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {
        size_t cap = CAM_W * CAM_H / 2;   // JPEG QVGA fica bem abaixo disso
        if (len <= cap) { memcpy(web_jpeg, buf, len); web_jpeg_len = len; }
        xSemaphoreGive(web_mutex);
    }
}

static esp_err_t handler_index(httpd_req_t* req) {
    // pagina com overlay: zonas desenhadas sobre o video, centroides ao
    // vivo e letreiro de estado (pisca vermelho no alerta)
    static const char html[] =
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>AssemblyGuard</title><style>"
        "body{font-family:sans-serif;background:#111;color:#eee;margin:0;"
        "display:flex;flex-direction:column;align-items:center}"
        "h1{font-size:1.1rem;margin:.6rem}"
        "#w{position:relative;width:min(96vw,640px)}"
        "#v{width:100%;display:block;border:2px solid #444;border-radius:6px;"
        "box-sizing:border-box}"
        "#c{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}"
        "#b{font-size:.95rem;background:#222;padding:.4rem 1rem;"
        "border-radius:6px;margin:.5rem;min-height:1.2em}"
        "#b.al{background:#a00}"
        "pre{background:#222;padding:.5rem 1rem;border-radius:6px;"
        "font-size:.8rem;max-width:96vw;overflow-x:auto;min-height:1em}"
        "</style></head><body><h1>AssemblyGuard &mdash; Bloq Volt</h1>"
        "<div id='w'><img id='v'><canvas id='c'></canvas></div>"
        "<div id='b'>aguardando...</div><pre id='s'></pre>"
        "<script>"
        "var Z=null,CO={peca:'#3af',pilha:'#fd3',ferro_solda:'#f55',"
        "aplicador_cola:'#5f5'};"
        "var v=document.getElementById('v'),c=document.getElementById('c'),"
        "b=document.getElementById('b');"
        "v.src='http://'+location.hostname+':81/stream';"
        "fetch('/zonas').then(function(r){return r.json()})"
        ".then(function(j){Z=j});"
        "function dets(t){var a=[];t.split(';').forEach(function(p){"
        "p=p.trim();if(!p)return;var m=p.split(' ');if(m.length<3)return;"
        "var xy=m[2].replace('(','').replace(')','').split(',');"
        "a.push({l:m[0],c:m[1],x:+xy[0],y:+xy[1]})});return a}"
        "function draw(st){var W=v.clientWidth,H=v.clientHeight;"
        "c.width=W;c.height=H;var g=c.getContext('2d');g.clearRect(0,0,W,H);"
        "g.font='12px sans-serif';"
        "if(Z){for(var n in Z){var z=Z[n];"
        "var on=(n=='entrada'&&st.pilha_entrada)||(n=='saida'&&st.pilha_saida)"
        "||(n=='bancada'&&st.pecas_bancada>0)"
        "||(n=='descanso_ferro'&&st.ferro_em_uso);"
        "g.strokeStyle=on?'#0f0':'#999';g.lineWidth=on?3:1;"
        "g.strokeRect(z[0]*W,z[1]*H,(z[2]-z[0])*W,(z[3]-z[1])*H);"
        "g.fillStyle=g.strokeStyle;g.fillText(n,z[0]*W+4,z[1]*H+14)}}"
        "dets(st.dets||'').forEach(function(d){var x=d.x*W,y=d.y*H;"
        "g.strokeStyle=CO[d.l]||'#fff';g.lineWidth=3;g.beginPath();"
        "g.arc(x,y,9,0,7);g.stroke();g.fillStyle=g.strokeStyle;"
        "g.fillText(d.l+' '+d.c+' ('+d.x.toFixed(2)+','+d.y.toFixed(2)+')',"
        "Math.min(x+12,W-150),Math.max(y-8,12))})}"
        "setInterval(async function(){try{"
        "var r=await fetch('/status');var st=await r.json();"
        "b.textContent='estado: '+st.estado+' | leitura: '+st.leitura"
        "+' | pecas: '+st.pecas_bancada+' | ciclos: '+st.ciclos"
        "+' | '+st.latencia_ms+' ms';"
        "b.className=st.alerta?'al':'';"
        "document.getElementById('s').textContent=st.dets||'';"
        "draw(st)}catch(e){}},600);"
        "</script></body></html>";
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, html, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t handler_zonas(httpd_req_t* req) {
    char buf[280];
    snprintf(buf, sizeof(buf),
             "{\"entrada\":[%.2f,%.2f,%.2f,%.2f],"
             "\"bancada\":[%.2f,%.2f,%.2f,%.2f],"
             "\"descanso_ferro\":[%.2f,%.2f,%.2f,%.2f],"
             "\"saida\":[%.2f,%.2f,%.2f,%.2f]}",
             ZONA_ENTRADA.x1, ZONA_ENTRADA.y1, ZONA_ENTRADA.x2, ZONA_ENTRADA.y2,
             ZONA_BANCADA.x1, ZONA_BANCADA.y1, ZONA_BANCADA.x2, ZONA_BANCADA.y2,
             ZONA_FERRO.x1,   ZONA_FERRO.y1,   ZONA_FERRO.x2,   ZONA_FERRO.y2,
             ZONA_SAIDA.x1,   ZONA_SAIDA.y1,   ZONA_SAIDA.x2,   ZONA_SAIDA.y2);
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, buf, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t handler_status(httpd_req_t* req) {
    char buf[512];
    if (xSemaphoreTake(web_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
        strlcpy(buf, web_status, sizeof(buf));
        xSemaphoreGive(web_mutex);
    } else {
        strlcpy(buf, "{}", sizeof(buf));
    }
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, buf, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t handler_stream(httpd_req_t* req) {
    httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=fr");
    char head[64];
    // buffer local para nao segurar o mutex durante o envio pela rede
    uint8_t* tmp = (uint8_t*)ps_malloc(CAM_W * CAM_H / 2);
    if (!tmp) return ESP_FAIL;

    while (true) {
        size_t len = 0;
        if (xSemaphoreTake(web_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
            len = web_jpeg_len;
            if (len) memcpy(tmp, web_jpeg, len);
            xSemaphoreGive(web_mutex);
        }
        if (len) {
            int n = snprintf(head, sizeof(head),
                             "--fr\r\nContent-Type: image/jpeg\r\n"
                             "Content-Length: %u\r\n\r\n", (unsigned)len);
            if (httpd_resp_send_chunk(req, head, n) != ESP_OK) break;
            if (httpd_resp_send_chunk(req, (const char*)tmp, len) != ESP_OK) break;
            if (httpd_resp_send_chunk(req, "\r\n", 2) != ESP_OK) break;
        }
        vTaskDelay(pdMS_TO_TICKS(150));   // ~6 fps de stream basta
    }
    free(tmp);
    return ESP_OK;
}

static void iniciar_web() {
    web_mutex = xSemaphoreCreateMutex();
    web_jpeg = (uint8_t*)ps_malloc(CAM_W * CAM_H / 2);
    if (!web_jpeg) { Serial.println("AVISO: sem PSRAM p/ stream"); return; }

    WiFi.persistent(false);       // nao gastar flash regravando config
    if (strlen(WIFI_STA_SSID) > 0) {
        WiFi.mode(WIFI_STA);
        WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASS);
        Serial.printf("conectando em '%s'", WIFI_STA_SSID);
        for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) {
            delay(500); Serial.print('.');
        }
        Serial.printf("\nmonitor: http://%s/\n",
                      WiFi.localIP().toString().c_str());
    } else {
        WiFi.mode(WIFI_AP);
        // canal fixo 6, ate 2 clientes: beacon mais estavel sob carga
        WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASS, 6, 0, 2);
        Serial.printf("rede propria '%s' senha '%s'\n",
                      WIFI_AP_SSID, WIFI_AP_PASS);
        Serial.printf("monitor: http://%s/\n",
                      WiFi.softAPIP().toString().c_str());
    }
    WiFi.setSleep(false);         // radio sempre acordado (o AP some menos)
    // 13 dBm alcanca a bancada e corta os picos de corrente do TX -
    // pico de corrente + camera + CPU 240 MHz e o que derruba a placa
    // (brownout) quando o cabo/porta USB e fraco
    WiFi.setTxPower(WIFI_POWER_13dBm);

    // pagina e status na porta 80; o STREAM numa porta separada (81):
    // o handler do stream fica em loop infinito e monopoliza a task do
    // servidor - na mesma porta, o /status nunca seria atendido
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.max_uri_handlers = 4;
    httpd_handle_t srv = nullptr;
    if (httpd_start(&srv, &cfg) == ESP_OK) {
        httpd_uri_t u1 = {.uri = "/", .method = HTTP_GET,
                          .handler = handler_index, .user_ctx = nullptr};
        httpd_uri_t u3 = {.uri = "/status", .method = HTTP_GET,
                          .handler = handler_status, .user_ctx = nullptr};
        httpd_uri_t u4 = {.uri = "/zonas", .method = HTTP_GET,
                          .handler = handler_zonas, .user_ctx = nullptr};
        httpd_register_uri_handler(srv, &u1);
        httpd_register_uri_handler(srv, &u3);
        httpd_register_uri_handler(srv, &u4);
    }

    httpd_config_t cfg2 = HTTPD_DEFAULT_CONFIG();
    cfg2.server_port = 81;
    cfg2.ctrl_port   = 32769;   // precisa diferir do servidor da porta 80
    httpd_handle_t srv2 = nullptr;
    if (httpd_start(&srv2, &cfg2) == ESP_OK) {
        httpd_uri_t u2 = {.uri = "/stream", .method = HTTP_GET,
                          .handler = handler_stream, .user_ctx = nullptr};
        httpd_register_uri_handler(srv2, &u2);
    }
}

// vigia: a cada 5 s confere se a rede continua de pe e reergue se caiu
static void manter_wifi() {
    static uint32_t t_ult = 0;
    if (millis() - t_ult < 5000) return;
    t_ult = millis();

    if (strlen(WIFI_STA_SSID) > 0) {
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("wifi caiu - reconectando...");
            WiFi.reconnect();
        }
    } else if (WiFi.softAPIP() == IPAddress((uint32_t)0)) {
        Serial.println("AP caiu - recriando rede...");
        WiFi.mode(WIFI_AP);
        WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASS, 6, 0, 2);
        WiFi.setSleep(false);
        WiFi.setTxPower(WIFI_POWER_13dBm);
    }
}
#endif  // USA_WEB

// ---------------------------------------------------------------------
// SETUP
// ---------------------------------------------------------------------
void setup() {
    Serial.begin(115200);
    delay(1500);
    Serial.println("\n== AssemblyGuard V2 - XIAO ESP32S3 ==");

    camera_config_t cfg = {};
    cfg.ledc_channel = LEDC_CHANNEL_0;
    cfg.ledc_timer   = LEDC_TIMER_0;
    cfg.pin_d0 = Y2_GPIO_NUM;  cfg.pin_d1 = Y3_GPIO_NUM;
    cfg.pin_d2 = Y4_GPIO_NUM;  cfg.pin_d3 = Y5_GPIO_NUM;
    cfg.pin_d4 = Y6_GPIO_NUM;  cfg.pin_d5 = Y7_GPIO_NUM;
    cfg.pin_d6 = Y8_GPIO_NUM;  cfg.pin_d7 = Y9_GPIO_NUM;
    cfg.pin_xclk = XCLK_GPIO_NUM;
    cfg.pin_pclk = PCLK_GPIO_NUM;
    cfg.pin_vsync = VSYNC_GPIO_NUM;
    cfg.pin_href = HREF_GPIO_NUM;
    cfg.pin_sccb_sda = SIOD_GPIO_NUM;
    cfg.pin_sccb_scl = SIOC_GPIO_NUM;
    cfg.pin_pwdn = PWDN_GPIO_NUM;
    cfg.pin_reset = RESET_GPIO_NUM;
    cfg.xclk_freq_hz = 20000000;
    cfg.pixel_format = PIXFORMAT_JPEG;      // JPEG: barato de capturar e
    cfg.frame_size   = FRAMESIZE_QVGA;      // ja pronto para salvar no SD
    cfg.jpeg_quality = 12;
    cfg.fb_count     = 1;
    cfg.fb_location  = CAMERA_FB_IN_PSRAM;  // <- PSRAM de 8 MB em acao
    cfg.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

    if (esp_camera_init(&cfg) != ESP_OK) {
        Serial.println("ERRO: camera nao inicializou");
        while (true) delay(1000);
    }

    // reforca o tamanho no sensor e descarta os frames de aquecimento
    // (os primeiros frames da OV2640 podem vir em outra resolucao)
    sensor_t* sens = esp_camera_sensor_get();
    if (sens) {
        sens->set_framesize(sens, FRAMESIZE_QVGA);
        sens->set_vflip(sens, CAM_VFLIP);
        sens->set_hmirror(sens, CAM_HMIRROR);
    }
    for (int i = 0; i < 3; i++) {
        camera_fb_t* fb = esp_camera_fb_get();
        if (fb) esp_camera_fb_return(fb);
        delay(80);
    }

    rgb888 = (uint8_t*)ps_malloc(CAM_W * CAM_H * 3);
    if (!rgb888) {
        Serial.println("ERRO: sem PSRAM para o buffer RGB");
        while (true) delay(1000);
    }

#if USA_SD
    if (!SD.begin(PIN_SD_CS)) {
        Serial.println("AVISO: microSD nao montou - log desligado");
    } else {
        File f = SD.open("/assemblyguard_log.csv", FILE_APPEND);
        if (f) {
            f.println("ms,estado,leitura,pecas_bancada,ferro_em_uso,"
                      "pilha_entrada,pilha_bancada,pilha_saida,"
                      "alvo_x,alvo_y,latencia_ms,alerta");
            f.close();
        }
        Serial.println("microSD ok - /assemblyguard_log.csv");
    }
#endif
#if USA_LED && !USA_SD
    pinMode(PIN_LED, OUTPUT);
    digitalWrite(PIN_LED, HIGH);   // LED do XIAO e invertido (HIGH=apagado)
#endif

#if USA_WEB
    iniciar_web();
#endif

    t_inicio_ciclo = millis();
    Serial.println("modelo: " EI_CLASSIFIER_PROJECT_NAME);
    Serial.printf("entrada do modelo: %dx%d\n",
                  EI_CLASSIFIER_INPUT_WIDTH, EI_CLASSIFIER_INPUT_HEIGHT);
}

// ---------------------------------------------------------------------
// CAPTURA + CONVERSAO
// ---------------------------------------------------------------------
static int ei_camera_get_data(size_t offset, size_t length, float* out_ptr) {
    // rgb888 esta em BGR? nao: fmt2rgb888 entrega RGB na ordem R,G,B
    size_t ix = offset * 3;
    for (size_t i = 0; i < length; i++) {
        out_ptr[i] = (rgb888[ix + 0] << 16) + (rgb888[ix + 1] << 8) + rgb888[ix + 2];
        ix += 3;
    }
    return 0;
}

// le largura/altura do cabecalho SOF do JPEG, sem decodificar
static bool jpeg_dims(const uint8_t* b, size_t n, int* w, int* h) {
    for (size_t i = 2; i + 9 < n; ) {
        if (b[i] != 0xFF) { i++; continue; }
        uint8_t m = b[i + 1];
        if (m == 0xD8 || m == 0x01 || (m >= 0xD0 && m <= 0xD7)) { i += 2; continue; }
        if (m == 0xC0 || m == 0xC1 || m == 0xC2) {
            *h = (b[i + 5] << 8) | b[i + 6];
            *w = (b[i + 7] << 8) | b[i + 8];
            return true;
        }
        if (m == 0xDA) break;                       // scan sem SOF antes: invalido
        i += 2 + ((b[i + 2] << 8) | b[i + 3]);
    }
    return false;
}

static bool capturar() {
    fb_atual = esp_camera_fb_get();
    if (!fb_atual) return false;
    // rgb888 tem espaco para CAM_W x CAM_H; um frame maior (1o frame do
    // sensor, header corrompido) estouraria o heap na decodificacao
    int w = 0, h = 0;
    if (fb_atual->format != PIXFORMAT_JPEG ||
        !jpeg_dims(fb_atual->buf, fb_atual->len, &w, &h) ||
        w != CAM_W || h != CAM_H) {
        Serial.printf("frame descartado: %dx%d, %u bytes\n",
                      w, h, (unsigned)fb_atual->len);
        return false;
    }
    return fmt2rgb888(fb_atual->buf, fb_atual->len, PIXFORMAT_JPEG, rgb888);
}

static void soltar_frame() {
    if (fb_atual) { esp_camera_fb_return(fb_atual); fb_atual = nullptr; }
}

// ---------------------------------------------------------------------
// ALERTA + LOG
// ---------------------------------------------------------------------
static void salvar_jpeg_alerta(uint32_t ms) {
#if USA_SD
    if (!fb_atual) return;
    char nome[48];
    snprintf(nome, sizeof(nome), "/alerta_%lu.jpg", (unsigned long)ms);
    File f = SD.open(nome, FILE_WRITE);
    if (f) { f.write(fb_atual->buf, fb_atual->len); f.close(); }
#endif
}

static void log_csv(const Leitura& L, Etapa leitura, uint32_t lat_ms,
                    bool alerta, uint32_t ms) {
#if USA_SD
    File f = SD.open("/assemblyguard_log.csv", FILE_APPEND);
    if (!f) return;
    f.printf("%lu,%s,%s,%d,%d,%d,%d,%d,%.2f,%.2f,%lu,%d\n",
             (unsigned long)ms, NOME_ETAPA[estado], NOME_ETAPA[leitura],
             L.pecas_bancada, L.ferro_em_uso, L.pilha_entrada,
             L.pilha_bancada, L.pilha_saida, L.alvo_x, L.alvo_y,
             (unsigned long)lat_ms, alerta);
    f.close();
#endif
}

static void sinal_alerta(const Leitura& L, Etapa leitura) {
    Serial.printf(">>> ALERTA: esperado '%s' (ou '%s'), detectado '%s' "
                  "em (%.2f, %.2f)\n",
                  NOME_ETAPA[estado], NOME_ETAPA[proxima(estado)],
                  NOME_ETAPA[leitura], L.alvo_x, L.alvo_y);
#if USA_LED
    pinMode(PIN_LED, OUTPUT);
    for (int i = 0; i < 3; i++) {           // pisca curto; ver nota GPIO21
        digitalWrite(PIN_LED, LOW);  delay(60);
        digitalWrite(PIN_LED, HIGH); delay(60);
    }
#endif
}

// ---------------------------------------------------------------------
// INFERENCIA DA ETAPA a partir dos objetos
// ---------------------------------------------------------------------
static Etapa inferir_etapa(const Leitura& L) {
    if (L.pilha_saida)        return FIM;
    if (L.pilha_bancada)      return EMPILHAGEM;  // pilha se formando na bancada
    if (L.ferro_em_uso)       return SOLDA;
    if (L.pecas_bancada >= 3) return MONTAGEM;
    if (L.pecas_bancada >= 1) return PREPARO;
    return ESPERA;
}

// ---------------------------------------------------------------------
// LOOP
// ---------------------------------------------------------------------
void loop() {
    if (!capturar()) { soltar_frame(); delay(100); return; }

    ei::signal_t signal;
    signal.total_length = EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT;
    signal.get_data = &ei_camera_get_data;

    // redimensiona 320x240 -> entrada do modelo (squash, como no treino)
    ei::image::processing::crop_and_interpolate_rgb888(
        rgb888, CAM_W, CAM_H,
        rgb888, EI_CLASSIFIER_INPUT_WIDTH, EI_CLASSIFIER_INPUT_HEIGHT);

    ei_impulse_result_t result = {0};
    uint32_t t0 = millis();
    EI_IMPULSE_ERROR err = run_classifier(&signal, &result, false);
    uint32_t lat = millis() - t0;
    if (err != EI_IMPULSE_OK) { soltar_frame(); return; }

    // ------- agrega os centroides em sinais de processo -------
    Leitura L = {};
    L.alvo_x = -1; L.alvo_y = -1;
    char dets[160]; int dlen = 0; dets[0] = 0;   // p/ mostrar no painel web
    for (uint32_t i = 0; i < result.bounding_boxes_count; i++) {
        auto& bb = result.bounding_boxes[i];
        if (bb.value < CONF_MIN) continue;
        float cx = (bb.x + bb.width * 0.5f) / EI_CLASSIFIER_INPUT_WIDTH;
        float cy = (bb.y + bb.height * 0.5f) / EI_CLASSIFIER_INPUT_HEIGHT;
        if (dlen < (int)sizeof(dets) - 40)
            dlen += snprintf(dets + dlen, sizeof(dets) - dlen,
                             "%s %.2f (%.2f,%.2f); ", bb.label, bb.value, cx, cy);

        if (strcmp(bb.label, "peca") == 0) {
            if (dentro(ZONA_BANCADA, cx, cy)) L.pecas_bancada++;
        } else if (strcmp(bb.label, "ferro_solda") == 0) {
            if (!dentro(ZONA_FERRO, cx, cy)) {
                L.ferro_em_uso = true; L.alvo_x = cx; L.alvo_y = cy;
            }
        } else if (strcmp(bb.label, "pilha") == 0) {
            if (dentro(ZONA_ENTRADA, cx, cy))      L.pilha_entrada = true;
            else if (dentro(ZONA_SAIDA, cx, cy)) { L.pilha_saida = true;
                                                   L.alvo_x = cx; L.alvo_y = cy; }
            else                                 { L.pilha_bancada = true;
                                                   L.alvo_x = cx; L.alvo_y = cy; }
        }
        Serial.printf("  %s (%.2f) em (%.2f, %.2f)\n", bb.label, bb.value, cx, cy);
    }

    Etapa leitura = inferir_etapa(L);

    // ------- confirmacao temporal -------
    if (leitura == leitura_ant) frames_iguais++;
    else                        frames_iguais = 1;
    leitura_ant = leitura;

    // Regra simplificada: o estado so anda PARA FRENTE na sequencia.
    // - leitura a frente do estado (confirmada) -> avanca ate ela;
    //   se pulou etapa no caminho, registra alerta de desvio mas NAO
    //   trava (perder uma etapa de vista nao pode impedir o fim);
    // - pilha na SAIDA e sempre FIM -> fecha o ciclo;
    // - depois do FIM, leitura de ESPERA rearma para o proximo ciclo;
    // - leitura atras do estado e ignorada (nao regride, nao alerta).
    bool alerta = false;
    if (frames_iguais >= N_CONFIRMA) {
        if (leitura > estado) {
            if (leitura > proxima(estado)) {        // pulou etapa: desvio
                alerta = true;
                sinal_alerta(L, leitura);
                salvar_jpeg_alerta(millis());
            }
            estado = leitura;
            Serial.printf("== etapa: %s\n", NOME_ETAPA[estado]);
            if (estado == FIM) {
                ciclos_ok++;
                Serial.printf("== CICLO %d COMPLETO em %.1f s\n", ciclos_ok,
                              (millis() - t_inicio_ciclo) / 1000.0f);
            }
        } else if (estado == FIM && leitura == ESPERA) {
            estado = ESPERA;                        // pronto p/ novo ciclo
            t_inicio_ciclo = millis();
            Serial.println("== novo ciclo");
        }
    }

    log_csv(L, leitura, lat, alerta, millis());
    Serial.printf("estado=%s leitura=%s pecas=%d lat=%lums\n",
                  NOME_ETAPA[estado], NOME_ETAPA[leitura],
                  L.pecas_bancada, (unsigned long)lat);

#if USA_WEB
    // publica o frame cru (JPEG da camera) e o status para o navegador
    web_publica_frame(fb_atual->buf, fb_atual->len);
    if (xSemaphoreTake(web_mutex, pdMS_TO_TICKS(20)) == pdTRUE) {
        snprintf(web_status, sizeof(web_status),
                 "{\"estado\":\"%s\",\"leitura\":\"%s\",\"pecas_bancada\":%d,"
                 "\"ferro_em_uso\":%s,\"pilha_entrada\":%s,"
                 "\"pilha_bancada\":%s,\"pilha_saida\":%s,"
                 "\"ciclos\":%d,\"latencia_ms\":%lu,\"alerta\":%s,"
                 "\"dets\":\"%s\"}",
                 NOME_ETAPA[estado], NOME_ETAPA[leitura], L.pecas_bancada,
                 L.ferro_em_uso ? "true" : "false",
                 L.pilha_entrada ? "true" : "false",
                 L.pilha_bancada ? "true" : "false",
                 L.pilha_saida ? "true" : "false",
                 ciclos_ok, (unsigned long)lat, alerta ? "true" : "false",
                 dets);
        xSemaphoreGive(web_mutex);
    }
    manter_wifi();
#endif

    soltar_frame();
}
