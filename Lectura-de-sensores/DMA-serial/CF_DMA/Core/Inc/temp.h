#ifndef __TEMP_H__
#define __TEMP_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include <math.h>
#include <stdint.h>

typedef struct {
uint16_t tempRead[4];
} temp_t;

temp_t getTemp(uint32_t *adc);

uint16_t convertAnalogToTemperature(uint16_t temp);

#ifdef __cplusplus
}
#endif
#endif