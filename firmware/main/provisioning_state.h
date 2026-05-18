#pragma once

#include <stdint.h>

/* ─── Provisioning state machine ───────────────────────────────────────────── */

typedef enum {
    PROV_STATE_UNPROVISIONED   = 0,  /* factory default, NVS empty */
    PROV_STATE_BLE_CONNECTED   = 1,  /* parent app has connected */
    PROV_STATE_CREDS_LOADED    = 2,  /* all three fields written to RAM */
    PROV_STATE_COMMITTING      = 3,  /* PROVISION_CMD received, writing NVS */
    PROV_STATE_WIFI_CONNECTING = 4,  /* NVS written, connecting WiFi */
    PROV_STATE_MQTT_CONNECTING = 5,  /* WiFi up, connecting MQTT */
    PROV_STATE_VALIDATING      = 6,  /* MQTT up, waiting for heartbeat ack */
    PROV_STATE_READY           = 7,  /* fully provisioned + validated */
    PROV_STATE_ERROR           = 8,  /* unrecoverable error, BLE restart */
} prov_state_t;

/* ─── Error codes ───────────────────────────────────────────────────────────── */

typedef enum {
    PROV_ERR_NONE              = 0,
    PROV_ERR_NVS_WRITE         = 1,
    PROV_ERR_WIFI_AUTH         = 2,  /* wrong password */
    PROV_ERR_WIFI_NO_AP        = 3,  /* SSID not found */
    PROV_ERR_WIFI_TIMEOUT      = 4,
    PROV_ERR_MQTT_AUTH         = 5,  /* broker rejected credentials */
    PROV_ERR_MQTT_TIMEOUT      = 6,
    PROV_ERR_HEARTBEAT_TIMEOUT = 7,
    PROV_ERR_BLE_TIMEOUT       = 8,  /* 10-min advertising timeout */
} prov_error_t;

/* ─── LED states ────────────────────────────────────────────────────────────── */

typedef enum {
    LED_OFF                    = 0,
    LED_SLOW_BLUE_PULSE,        /* UNPROVISIONED — advertising */
    LED_SOLID_BLUE,             /* BLE_CONNECTED */
    LED_FAST_BLUE_PULSE,        /* CREDS_LOADED — waiting for commit */
    LED_YELLOW_BREATHE,         /* COMMITTING / WIFI_CONNECTING */
    LED_CYAN_BREATHE,           /* MQTT_CONNECTING / VALIDATING */
    LED_SOLID_GREEN,            /* READY */
    LED_FAST_RED_FLASH,         /* ERROR */
    LED_WHITE_TRIPLE_FLASH,     /* Factory reset confirmation */
} led_pattern_t;

/* ─── BLE service + characteristic UUIDs ───────────────────────────────────── */

/*
 * Service: BOBOLOO_PROV_SVC_UUID
 * Characteristics:
 *   DEVICE_INFO  — read-only: JSON {"factory_device_id":"<id>","fw_version":"<v>"}
 *   WIFI_SSID    — write-only (no-rsp), max 32 bytes
 *   WIFI_PASS    — write-only (no-rsp), max 64 bytes
 *   TOY_API_KEY  — write-only (no-rsp), exactly 64 hex chars (256-bit key)
 *   PROV_CMD     — write-only (no-rsp): 0x01 = commit, 0x02 = cancel/reset
 *   PROV_STATUS  — notify: prov_ble_status_t packed struct
 */

/* 128-bit base UUID: f0ad0001-xxxx-4a4b-8c8d-9e0f1a2b3c4d */
#define PROV_SVC_UUID128       {0x4d,0x3c,0x2b,0x1a,0x0f,0x9e,0x8d,0x8c,\
                                0x4b,0x4a,0x00,0x00,0xad,0xf0,0x00,0x00}
#define CHAR_UUID_DEVICE_INFO  0x0001
#define CHAR_UUID_WIFI_SSID    0x0002
#define CHAR_UUID_WIFI_PASS    0x0003
#define CHAR_UUID_TOY_API_KEY  0x0004
#define CHAR_UUID_PROV_CMD     0x0005
#define CHAR_UUID_PROV_STATUS  0x0006

/* PROV_CMD byte values */
#define PROV_CMD_COMMIT        0x01
#define PROV_CMD_CANCEL        0x02

/* ─── BLE status notification payload (6 bytes, packed) ────────────────────── */

typedef struct __attribute__((packed)) {
    uint8_t  state;     /* prov_state_t */
    uint8_t  error;     /* prov_error_t */
    uint8_t  reserved[4];
} prov_ble_status_t;

/* ─── Provisioning RAM buffer ───────────────────────────────────────────────── */

#define WIFI_SSID_MAX    32
#define WIFI_PASS_MAX    64
#define TOY_API_KEY_LEN  64  /* hex string, no null in BLE write */
#define TOY_API_KEY_SZ   65  /* +1 null terminator in RAM */

typedef struct {
    char ssid[WIFI_SSID_MAX + 1];
    char password[WIFI_PASS_MAX + 1];
    char api_key[TOY_API_KEY_SZ];
    uint8_t ssid_set    : 1;
    uint8_t pass_set    : 1;
    uint8_t api_key_set : 1;
} prov_credentials_t;

/* ─── Timing constants ──────────────────────────────────────────────────────── */

#define PROV_BLE_TIMEOUT_MS         (10 * 60 * 1000)   /* 10 min advertising */
#define PROV_WIFI_CONNECT_TIMEOUT_MS (20 * 1000)        /* 20 s */
#define PROV_MQTT_CONNECT_TIMEOUT_MS (15 * 1000)        /* 15 s */
#define PROV_HEARTBEAT_TIMEOUT_MS   (10 * 1000)         /* 10 s */
#define PROV_RESET_HOLD_MS          (5  * 1000)         /* 5 s for factory reset */
#define PROV_WIFI_MAX_RETRIES        5
