#include "reset_button.h"
#include "led_control.h"
#include "nvs_storage.h"
#include "provisioning_state.h"
#include <driver/gpio.h>
#include <freertos/FreeRTOS.h>
#include <freertos/timers.h>
#include <esp_log.h>
#include <esp_system.h>

#define TAG "reset_btn"

static int           s_gpio        = -1;
static TimerHandle_t s_hold_timer  = NULL;

static void hold_timer_cb(TimerHandle_t t)
{
    /* 5 s elapsed with button still held — factory reset */
    if (gpio_get_level(s_gpio) == 0) {
        ESP_LOGW(TAG, "Factory reset triggered");
        led_set_pattern(LED_WHITE_TRIPLE_FLASH);
        vTaskDelay(pdMS_TO_TICKS(2000));
        nvs_erase_credentials();
        esp_restart();
    }
}

static void IRAM_ATTR gpio_isr_handler(void *arg)
{
    BaseType_t woken = pdFALSE;
    if (gpio_get_level(s_gpio) == 0) {
        /* Button pressed — start hold timer */
        xTimerResetFromISR(s_hold_timer, &woken);
        xTimerStartFromISR(s_hold_timer, &woken);
    } else {
        /* Button released before timer fired — cancel */
        xTimerStopFromISR(s_hold_timer, &woken);
    }
    if (woken) portYIELD_FROM_ISR();
}

void reset_button_init(int gpio_num)
{
    s_gpio = gpio_num;

    gpio_config_t cfg = {
        .pin_bit_mask = BIT64(gpio_num),
        .mode         = GPIO_MODE_INPUT,
        .pull_up_en   = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type    = GPIO_INTR_ANYEDGE,
    };
    gpio_config(&cfg);
    gpio_install_isr_service(0);
    gpio_isr_handler_add((gpio_num_t)gpio_num, gpio_isr_handler, NULL);

    s_hold_timer = xTimerCreate("rst_hold",
                                pdMS_TO_TICKS(PROV_RESET_HOLD_MS),
                                pdFALSE, NULL, hold_timer_cb);
}
