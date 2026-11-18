#ifndef __HUM_H__
#define __HUM_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include <stdint.h>

typedef struct {
    GPIO_TypeDef *port;
    uint16_t pin;
} humsensor_t;

void DHT11_Init(void);

int8_t DHT11_ReadValues(const humsensor_t *s, float *temperature, float *humidity);

#ifdef __cplusplus
}
#endif
#endif