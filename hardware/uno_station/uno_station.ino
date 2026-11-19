// Hollow Uplink: a real "surface machine" on an Arduino Uno.
//
// Pretends to be a BX-1 field station from a made-up vendor ("Burrow Works"): it prints one
// JSON line every 2 s over USB serial in its own format, and switches a relay when told to.
// It knows nothing about Hollow Uplink. tools/uno_bridge.py forwards its lines to MQTT, and the
// gateway fingerprints, translates and signs them like any other machine.
//
// Wiring (keyes modules: "-" = GND, middle/"+" = 5V, "S" or "AO"/"DO" = signal):
//   MQ-2 gas sensor   AO -> A0
//   DHT11             S  -> D2   (humidity; temperature only if the 18B20 is missing)
//   18B20 temperature S  -> D4   (keyes module has the 4.7k pull-up built in)
//   Photoresistor     S  -> A1
//   Crash sensor (lever switch) S -> D3   (the box's "hatch": the lid presses the lever;
//                                          a reed switch or Hall sensor + magnet works too)
//   5V relay          S  -> D7   (built-in LED on pin 13 mirrors it)
//
// Libraries (Arduino IDE > Tools > Manage Libraries): "DHT sensor library" by Adafruit
// (and "Adafruit Unified Sensor" when it asks), "OneWire", and "DallasTemperature".
//
// Serial in (9600 baud, one per line): R1 = relay on, R0 = relay off.

#include <DHT.h>
#include <OneWire.h>
#include <DallasTemperature.h>

const int PIN_GAS = A0;
const int PIN_LIGHT = A1;
const int PIN_DHT = 2;
const int PIN_HATCH = 3;
const int PIN_DS18B20 = 4;
const int PIN_RELAY = 7;
const int PIN_LED = 13;

// The MQ-2 heater needs time before readings settle. 60 s is the minimum for a demo;
// a few minutes is better. The baseline is the clean-air level measured right after warm-up,
// so power the box up in normal room air.
const unsigned long WARMUP_MS = 60000UL;
const unsigned long REPORT_MS = 2000UL;
const int BASELINE_SAMPLES = 20;

// Most keyes crash/reed/Hall modules pull the signal LOW when pressed or near a magnet (hatch closed).
// If "hatch" reads backwards, flip this.
const int HATCH_CLOSED_LEVEL = LOW;

// Many relay boards switch on a HIGH input; some are active-LOW. Flip this if it's backwards.
const int RELAY_ON_LEVEL = HIGH;

DHT dht(PIN_DHT, DHT11);
OneWire oneWire(PIN_DS18B20);
DallasTemperature ds(&oneWire);
int gasBase = 0;          // 0 until warm-up finishes
bool relayOn = false;
unsigned long lastReport = 0;
char cmdBuf[16];
byte cmdLen = 0;

int readGas() {
  // Average a few reads; the MQ-2 output is noisy.
  long sum = 0;
  for (int i = 0; i < 8; i++) {
    sum += analogRead(PIN_GAS);
    delay(2);
  }
  return (int)(sum / 8);
}

void setRelay(bool on) {
  relayOn = on;
  digitalWrite(PIN_RELAY, on ? RELAY_ON_LEVEL : !RELAY_ON_LEVEL);
  digitalWrite(PIN_LED, on ? HIGH : LOW);
}

void handleCommand(const char *cmd) {
  if (strcmp(cmd, "R1") == 0) setRelay(true);
  else if (strcmp(cmd, "R0") == 0) setRelay(false);
  else {
    Serial.print(F("# unknown command: "));
    Serial.println(cmd);
    return;
  }
  Serial.print(F("# relay "));
  Serial.println(relayOn ? F("on") : F("off"));
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        handleCommand(cmdBuf);
        cmdLen = 0;
      }
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

void calibrate() {
  long sum = 0;
  for (int i = 0; i < BASELINE_SAMPLES; i++) {
    sum += readGas();
    delay(100);
  }
  gasBase = max(1, (int)(sum / BASELINE_SAMPLES));
  Serial.print(F("# gas baseline "));
  Serial.println(gasBase);
}

void report() {
  float t = dht.readTemperature();
  float rh = dht.readHumidity();
  // The 18B20 conversion was started at the last report (it takes up to 750 ms), so it's ready now.
  float dsT = ds.getTempCByIndex(0);
  ds.requestTemperatures();
  // -127 = no sensor answering; 85 = the power-on value before a first conversion.
  bool dsOk = dsT != DEVICE_DISCONNECTED_C && dsT != 85.0;
  bool warming = gasBase == 0;

  // Vendor format: nested blocks, raw ADC counts, and the device's own clean-air baseline.
  Serial.print(F("{\"dev\":\"BX-1\",\"fw\":\"1.0\",\"up_s\":"));
  Serial.print(millis() / 1000);
  Serial.print(F(",\"gas\":{\"adc\":"));
  Serial.print(readGas());
  Serial.print(F(",\"base\":"));
  Serial.print(gasBase);
  Serial.print(F(",\"warm\":"));
  Serial.print(warming ? 1 : 0);
  Serial.print(F("},\"dht\":{"));
  if (isnan(t) || isnan(rh)) {
    Serial.print(F("\"err\":1"));          // DHT11 read failed this time; leave the values out
  } else {
    Serial.print(F("\"t\":"));
    Serial.print(t, 1);
    Serial.print(F(",\"rh\":"));
    Serial.print(rh, 0);
  }
  Serial.print(F("},\"ds\":{"));
  if (dsOk) {
    Serial.print(F("\"t\":"));
    Serial.print(dsT, 2);
  } else {
    Serial.print(F("\"err\":1"));
  }
  Serial.print(F("},\"light\":"));
  Serial.print(analogRead(PIN_LIGHT));
  Serial.print(F(",\"hatch\":"));
  Serial.print(digitalRead(PIN_HATCH) == HATCH_CLOSED_LEVEL ? 0 : 1);
  Serial.print(F(",\"relay\":"));
  Serial.print(relayOn ? 1 : 0);
  Serial.println(F("}"));
}

void setup() {
  Serial.begin(9600);
  pinMode(PIN_HATCH, INPUT);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_LED, OUTPUT);
  setRelay(false);
  dht.begin();
  ds.begin();
  ds.setWaitForConversion(false);   // don't block the loop for 750 ms
  ds.requestTemperatures();
  Serial.println(F("# BX-1 field station up; warming gas sensor"));
}

void loop() {
  readSerial();
  if (gasBase == 0 && millis() >= WARMUP_MS) calibrate();
  if (millis() - lastReport >= REPORT_MS) {
    lastReport = millis();
    report();
  }
}
