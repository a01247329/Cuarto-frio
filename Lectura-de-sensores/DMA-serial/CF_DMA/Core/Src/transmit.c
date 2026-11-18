#include <stdint.h>
#include <stdio.h>
#include "main.h"   
#include "transmit.h"
#include "usart.h"

/* XOR de todos los caracteres del cuerpo (lo que va entre '$' y '*') */
static uint8_t calcular_checksum(const char *cuerpo)
{
    uint8_t cs = 0;
    while (*cuerpo) {
        cs ^= (uint8_t)*cuerpo++;
    }
    return cs;
}

/* temperatura y humedad ya convertidas a enteros: 4.35 °C -> 435, 87.2 % -> 872 */
void enviar_medicion(const char *cuarto, const char *sensor, int temp_x100, int hum_x10)
{
    char cuerpo[48];
    char linea[64];
    snprintf(cuerpo, sizeof(cuerpo), "TH,%s,%s,%d,%d", cuarto, sensor, temp_x100, hum_x10);
    int n = snprintf(linea, sizeof(linea), "$%s*%02X\n", cuerpo, calcular_checksum(cuerpo));
    HAL_UART_Transmit(&huart2, (uint8_t *)linea, n, 100);
}