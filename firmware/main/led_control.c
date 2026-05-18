#include "led_control.h"
#include <freertos/FreeRTOS.h>
#include <freertos/timers.h>
#include <driver/gpio.h>
#include <esp_log.h>
#include <string.h>

#define TAG "led_control"

/*
 * Adjust for hardware: single-wire WS2812 data pin or discrete R/G/B GPIOs.
 * For WS2812, replace led_set_rgb() with your RMT/SPI driver call.
 */
#define LED_GPIO_R  GPIO_NUM_4
#define LED_GPIO_G  GPIO_NUM_5
#define LED_GPIO_B  GPIO_NUM_6

static TimerHandle_t   s_timer     = NULL;
static led_pattern_t   s_current   = LED_OFF;
static uint32_t        s_tick      = 0;

/* ─── Low-level RGB output ──────────────────────────────────────────────────── */

static void led_set_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    /* For discrete GPIOs: treat any non-zero value as on */
    gpio_set_level(LED_GPIO_R, r > 0 ? 1 : 0);
    gpio_set_level(LED_GPIO_G, g > 0 ? 1 : 0);
    gpio_set_level(LED_GPIO_B, b > 0 ? 1 : 0);
}

/* ─── Timer callback — drives all patterns ──────────────────────────────────── */

static void led_timer_cb(TimerHandle_t t)
{
    s_tick++;

    switch (s_current) {
    case LED_OFF:
        led_set_rgb(0, 0, 0);
        break;

    case LED_SLOW_BLUE_PULSE:
        /* 2 s period: 1 s on, 1 s off */
        led_set_rgb(0, 0, (s_tick % 4) < 2 ? 1 : 0);
        break;

    case LED_SOLID_BLUE:
        led_set_rgb(0, 0, 1);
        break;

    case LED_FAST_BLUE_PULSE:
        /* 500 ms period: 250 ms on, 250 ms off */
        led_set_rgb(0, 0, (s_tick % 2) < 1 ? 1 : 0);
        break;

    case LED_YELLOW_BREATHE:
        /* Simple yellow toggle at 1 s period */
        led_set_rgb(1, 1, 0);
        break;

    case LED_CYAN_BREATHE:
        led_set_rgb(0, 1, 1);
        break;

    case LED_SOLID_GREEN:
        led_set_rgb(0, 1, 0);
        break;

    case LED_FAST_RED_FLASH:
        /* 250 ms period */
        led_set_rgb((s_tick % 2) < 1 ? 1 : 0, 0, 0);
        break;

    case LED_WHITE_TRIPLE_FLASH: {
        /* Three short white flashes then long off */
        uint32_t phase = s_tick % 8;
        uint8_t on = (phase == 0 || phase == 2 || phase == 4) ? 1 : 0;
        led_set_rgb(on, on, on);
        break;
    }

    default:
        led_set_rgb(0, 0, 0);
        break;
    }
}

/* ─── Public API ────────────────────────────────────────────────────────────── */

void led_control_init(void)
{
    gpio_config_t cfg = {
        .pin_bit_mask = BIT64(LED_GPIO_R) | BIT64(LED_GPIO_G) | BIT64(LED_GPIO_B),
        .mode         = GPIO_MODE_OUTPUT,
        .pull_up_en   = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_DISABLE,
    };
    gpio_config(&cfg);

    /* 250 ms tick — sufficient granularity for all patterns */
    s_timer = xTimerCreate("led", pdMS_TO_TICKS(250), pdTRUE, NULL, led_timer_cb);
    if (s_timer) xTimerStart(s_timer, 0);
}

void led_set_pattern(led_pattern_t pattern)
{
    s_current = pattern;
    s_tick    = 0;
}

void led_set_prov_state(prov_state_t state, prov_error_t error)
{
    if (error != PROV_ERR_NONE) {
        led_set_pattern(LED_FAST_RED_FLASH);
        return;
    }

    switch (state) {
    case PROV_STATE_UNPROVISIONED:   led_set_pattern(LED_SLOW_BLUE_PULSE); break;
    case PROV_STATE_BLE_CONNECTED:   led_set_pattern(LED_SOLID_BLUE);      break;
    case PROV_STATE_CREDS_LOADED:    led_set_pattern(LED_FAST_BLUE_PULSE); break;
    case PROV_STATE_COMMITTING:      /* fall through */
    case PROV_STATE_WIFI_CONNECTING: led_set_pattern(LED_YELLOW_BREATHE);  break;
    case PROV_STATE_MQTT_CONNECTING: /* fall through */
    case PROV_STATE_VALIDATING:      led_set_pattern(LED_CYAN_BREATHE);    break;
    case PROV_STATE_READY:           led_set_pattern(LED_SOLID_GREEN);     break;
    case PROV_STATE_ERROR:           led_set_pattern(LED_FAST_RED_FLASH);  break;
    default:                         led_set_pattern(LED_OFF);             break;
    }
}
