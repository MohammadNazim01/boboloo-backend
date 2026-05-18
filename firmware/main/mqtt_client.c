#include "mqtt_client.h"
#include "provisioning_state.h"
#include <mqtt_client.h>
#include <freertos/FreeRTOS.h>
#include <freertos/event_groups.h>
#include <esp_log.h>
#include <string.h>
#include <stdio.h>

#define TAG "toy_mqtt"

/* Embed broker CA cert at build time via CMake EMBED_FILES */
extern const uint8_t emqx_ca_pem_start[] asm("_binary_emqx_ca_pem_start");
extern const uint8_t emqx_ca_pem_end[]   asm("_binary_emqx_ca_pem_end");

#define MQTT_CONNECTED_BIT BIT0
#define MQTT_FAIL_BIT      BIT1

static esp_mqtt_client_handle_t s_client         = NULL;
static EventGroupHandle_t       s_mqtt_events    = NULL;
static mqtt_message_cb_t        s_msg_cb         = NULL;
static char                     s_device_id[64]  = {0};

/* ─── Event handler ─────────────────────────────────────────────────────────── */

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *event_data)
{
    esp_mqtt_event_handle_t event = (esp_mqtt_event_handle_t)event_data;

    switch (id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        xEventGroupSetBits(s_mqtt_events, MQTT_CONNECTED_BIT);
        break;

    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        xEventGroupClearBits(s_mqtt_events, MQTT_CONNECTED_BIT);
        xEventGroupSetBits(s_mqtt_events, MQTT_FAIL_BIT);
        break;

    case MQTT_EVENT_DATA:
        if (s_msg_cb && event->topic && event->data) {
            /* topic is not null-terminated; copy to stack buffer */
            char topic_buf[128] = {0};
            int  tlen = event->topic_len < (int)sizeof(topic_buf) - 1
                            ? event->topic_len
                            : (int)sizeof(topic_buf) - 1;
            memcpy(topic_buf, event->topic, tlen);
            s_msg_cb(topic_buf, event->data, event->data_len);
        }
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error");
        xEventGroupSetBits(s_mqtt_events, MQTT_FAIL_BIT);
        break;

    default:
        break;
    }
}

/* ─── Public API ────────────────────────────────────────────────────────────── */

void mqtt_client_init(void)
{
    s_mqtt_events = xEventGroupCreate();
}

esp_err_t mqtt_connect(const char *broker_uri,
                       const char *factory_device_id,
                       const char *toy_api_key)
{
    strncpy(s_device_id, factory_device_id, sizeof(s_device_id) - 1);
    xEventGroupClearBits(s_mqtt_events, MQTT_CONNECTED_BIT | MQTT_FAIL_BIT);

    esp_mqtt_client_config_t cfg = {
        .broker.address.uri        = broker_uri,
        .broker.verification.certificate
            = (const char *)emqx_ca_pem_start,
        .credentials.username      = factory_device_id,
        .credentials.authentication.password = toy_api_key,
        .session.keepalive          = 60,
        .network.timeout_ms         = PROV_MQTT_CONNECT_TIMEOUT_MS,
    };

    if (s_client) {
        esp_mqtt_client_destroy(s_client);
        s_client = NULL;
    }

    s_client = esp_mqtt_client_init(&cfg);
    if (!s_client) return ESP_FAIL;

    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                   mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);

    EventBits_t bits = xEventGroupWaitBits(
        s_mqtt_events,
        MQTT_CONNECTED_BIT | MQTT_FAIL_BIT,
        pdFALSE, pdFALSE,
        pdMS_TO_TICKS(PROV_MQTT_CONNECT_TIMEOUT_MS));

    if (bits & MQTT_CONNECTED_BIT) return ESP_OK;

    ESP_LOGW(TAG, "MQTT connect failed (bits=0x%lx)", (unsigned long)bits);
    return (bits & MQTT_FAIL_BIT) ? ESP_FAIL : ESP_ERR_TIMEOUT;
}

void mqtt_disconnect(void)
{
    if (s_client) esp_mqtt_client_stop(s_client);
}

bool mqtt_is_connected(void)
{
    if (!s_mqtt_events) return false;
    return (xEventGroupGetBits(s_mqtt_events) & MQTT_CONNECTED_BIT) != 0;
}

esp_err_t mqtt_publish(const char *topic, const char *payload, int qos)
{
    if (!s_client) return ESP_ERR_INVALID_STATE;
    int msg_id = esp_mqtt_client_publish(s_client, topic, payload,
                                         strlen(payload), qos, 0);
    return msg_id >= 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t mqtt_subscribe_toy_topics(const char *device_id)
{
    if (!s_client) return ESP_ERR_INVALID_STATE;

    char topic[128];
    snprintf(topic, sizeof(topic), "boboloo/toy/%s/audio/out", device_id);
    esp_mqtt_client_subscribe(s_client, topic, 1);

    snprintf(topic, sizeof(topic), "boboloo/toy/%s/cmd", device_id);
    esp_mqtt_client_subscribe(s_client, topic, 1);

    return ESP_OK;
}

esp_err_t mqtt_send_heartbeat(const char *device_id, const char *fw_version)
{
    char topic[128];
    char payload[256];
    snprintf(topic,   sizeof(topic),
             "boboloo/toy/%s/status", device_id);
    snprintf(payload, sizeof(payload),
             "{\"status\":\"online\",\"fw_version\":\"%s\"}", fw_version);
    return mqtt_publish(topic, payload, 0);
}

void mqtt_set_message_callback(mqtt_message_cb_t cb)
{
    s_msg_cb = cb;
}
