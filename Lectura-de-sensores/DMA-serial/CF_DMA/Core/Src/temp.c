
#include "temp.h"

static float s_adc_filtered[4] = {0.0f, 0.0f, 0.0f, 0.0f};

temp_t getTemp(uint32_t *adc)
{
    temp_t t_sensor;
    for (int i = 0; i < 4; i++) {
        if (s_adc_filtered[i] <= 0.0f) {
            s_adc_filtered[i] = (float)adc[i];
        } else {
            // Filtro paso bajo IIR (alfa=0.30) para suprimir ruido eléctrico y fluctuaciones del ADC
            s_adc_filtered[i] = 0.30f * (float)adc[i] + 0.70f * s_adc_filtered[i];
        }
        t_sensor.tempRead[i] = convertAnalogToTemperature((uint16_t)(s_adc_filtered[i] + 0.5f));
    }
    return t_sensor;
}

uint16_t convertAnalogToTemperature(uint16_t temp)
{
  if (temp == 0 || temp >= 4095) return 0;
  float Vout = temp * (3.3 / 4095); 
  if (Vout <= 0.001f) return 0;
  float R_NTC = ((3.3f - Vout) * 10000.0f) / Vout; 
  float Temp_K = (298.15f * 3435.0f)/(298.15f * log(R_NTC / 10000.0f) + 3435.0f); 
  if (Temp_K < 233.15f) return 0; // Menor a -40 °C
  return ((uint16_t) ((Temp_K - 273.15f) * 100));  
}