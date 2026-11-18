
#include "hum.h"
#include "main.h"

#define DHT11_TIMEOUT_US 100

void DHT11_Init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static inline void delay_us(uint32_t us)
{
    uint32_t start = DWT->CYCCNT;
    uint32_t ticks = us * (SystemCoreClock / 1000000U);
    while ((DWT->CYCCNT - start) < ticks);
}

static void pin_output(const humsensor_t *s)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = s->pin;
    g.Mode = GPIO_MODE_OUTPUT_OD;
    g.Pull = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(s->port, &g);
}

static void pin_input(const humsensor_t *s)
{
    GPIO_InitTypeDef g = {0};
    g.Pin = s->pin;
    g.Mode = GPIO_MODE_INPUT;
    g.Pull = GPIO_PULLUP;
    HAL_GPIO_Init(s->port, &g);
}

static int8_t wait_level(const humsensor_t *s, GPIO_PinState level)
{
    uint32_t t = 0;
    while (HAL_GPIO_ReadPin(s->port, s->pin) != level) {
        if (t++ >= DHT11_TIMEOUT_US) return -1;
        delay_us(1);
    }
    return 0;
}

static int8_t read_byte(const humsensor_t *s, uint8_t *out)
{
    uint8_t v = 0;
    for (uint8_t j = 0; j < 8; j++) {
        if (wait_level(s, GPIO_PIN_SET) < 0) return -1;   // fin del nivel bajo
        delay_us(40);
        if (HAL_GPIO_ReadPin(s->port, s->pin))
            v |= (1 << (7 - j));
        if (wait_level(s, GPIO_PIN_RESET) < 0) return -1; // fin del nivel alto
    }
    *out = v;
    return 0;
}

int8_t DHT11_ReadValues(const humsensor_t *s, float *temperature, float *humidity)
{
    uint8_t b[5];

    // Señal de inicio
    pin_output(s);
    HAL_GPIO_WritePin(s->port, s->pin, GPIO_PIN_RESET);
    HAL_Delay(20);
    HAL_GPIO_WritePin(s->port, s->pin, GPIO_PIN_SET);
    pin_input(s);

    __disable_irq();

    // Respuesta: bajo ~80 us, alto ~80 us
    if (wait_level(s, GPIO_PIN_RESET) < 0 ||
        wait_level(s, GPIO_PIN_SET)   < 0 ||
        wait_level(s, GPIO_PIN_RESET) < 0) {
        __enable_irq();
        return -1;
    }

    for (uint8_t i = 0; i < 5; i++) {
        if (read_byte(s, &b[i]) < 0) {
            __enable_irq();
            return -1;
        }
    }
    __enable_irq();

    if ((uint8_t)(b[0] + b[1] + b[2] + b[3]) != b[4])
        return -2;

    if (humidity)    *humidity    = b[0] + b[1] / 10.0f;
    if (temperature) *temperature = b[2] + b[3] / 10.0f;
    return 0;
}