#ifndef __TRANSMIT_H__
#define __TRANSMIT_H__

#ifdef __cplusplus
extern "C" {
#endif

#include "main.h"
#include <math.h>
#include <stdint.h>

void enviar_medicion(const char *cuarto, const char *sensor, int temp_x100, int hum_x10);

#ifdef __cplusplus
}
#endif
#endif