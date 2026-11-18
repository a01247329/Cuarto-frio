
#include "temp.h"

temp_t getTemp(uint32_t *adc)
{
    temp_t t_sensor;
    t_sensor.tempRead[0] = convertAnalogToTemperature(adc[0]);
    t_sensor.tempRead[1] = convertAnalogToTemperature(adc[1]);
    t_sensor.tempRead[2] = convertAnalogToTemperature(adc[2]);
    t_sensor.tempRead[3] = convertAnalogToTemperature(adc[3]);

    return t_sensor;
}

uint16_t convertAnalogToTemperature(uint16_t temp)
{
  if (temp == 0 || temp >= 4095) return 0;
  float Vout = temp * (3.3 / 4095); 
  float R_NTC = (Vout * 10000) /(3.3 - Vout); 
  float Temp_K = (298.15 * 3435)/(298.15 * log(R_NTC / 10000) + 3435); 
  return ((uint16_t) ((Temp_K - 273.15) * 100));  
}