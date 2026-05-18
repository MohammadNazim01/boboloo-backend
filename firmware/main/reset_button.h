#pragma once

#include <stdbool.h>

/*
 * Reset button — hold for PROV_RESET_HOLD_MS (5 s) to trigger factory reset:
 *   1. LED → LED_WHITE_TRIPLE_FLASH
 *   2. NVS credentials erased
 *   3. esp_restart()
 *
 * Single-click (<500 ms) is ignored.
 * Monitored via a GPIO interrupt + FreeRTOS timer.
 */

/* gpio_num must be a valid input GPIO with internal pull-up available */
void reset_button_init(int gpio_num);
