#pragma once

#include <esp_err.h>
#include <stdbool.h>

/*
 * Toy-side MQTT client — TLS connection to EMQX Cloud.
 *
 * Credentials: username = factory_device_id, password = toy_api_key.
 * TLS: broker CA cert embedded in firmware (DER or PEM).
 * QoS: heartbeat at QoS 0, audio/in at QoS 1.
 *
 * During provisioning validation, mqtt_connect() + mqtt_send_heartbeat()
 * must succeed before ota_mark_valid() is called. On failure the provisioning
 * state machine rolls back.
 */

void      mqtt_client_init(void);

/* Synchronous connect — blocks until CONNECTED event or timeout.
 * broker_uri example: "mqtts://xxxx.emqxcloud.com:8883" */
esp_err_t mqtt_connect(const char *broker_uri,
                       const char *factory_device_id,
                       const char *toy_api_key);

void      mqtt_disconnect(void);
bool      mqtt_is_connected(void);

/* Publish audio response request.  topic = "boboloo/toy/{id}/audio/in" */
esp_err_t mqtt_publish(const char *topic, const char *payload, int qos);

/* Subscribe to audio/out and cmd topics */
esp_err_t mqtt_subscribe_toy_topics(const char *factory_device_id);

/* Send heartbeat to boboloo/toy/{id}/status */
esp_err_t mqtt_send_heartbeat(const char *factory_device_id,
                              const char *fw_version);

/* Register a callback for inbound messages (audio/out, cmd) */
typedef void (*mqtt_message_cb_t)(const char *topic, const char *payload,
                                  int payload_len);
void mqtt_set_message_callback(mqtt_message_cb_t cb);
