# miqro_can

This is a software implementation for reading CAN bus data from Citroen Jumper (tested in a 2020 model) and publishing it to an MQTT broker.

**WARNING: This software must not be used while driving. Use only when the vehicle is stationary. Use at your own risk!**

## Hardware Requirements

This software runs on a Raspberry Pi with a CAN bus interface module. You will need:

* A Raspberry Pi (any recent model should do)
* A CAN bus transceiver module (like [this one](https://amzn.to/49GcKpt) — affiliate link)
* Appropriate cabling to connect to your vehicle's CAN bus. You will need to interface the CAN-H and CAN-L signals from the "entertainment CAN bus". Follow the instructions on [page 24 of this document](https://www.esxnavi.de/assets/ig_vnc730_fi-ducato.pdf) to find the right cables in the dashboard. They can also be found in the "coach builder's socket" in the B-column, right side. Refer to pins 9 and 10 on page 3.15 (PDF page 87) in the "Fiat Ducato Converters' Manual" (found, e.g., [here](https://www.motorhomefun.co.uk/forum/threads/fiat-ducato-converters-manual.193886/)). 

## Installation

1. Create a virtual environment:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

2. Download this software:
   ```bash
   git clone https://github.com/danielfett/miqro_can.git
   cd miqro_can
   ```

3. Install dependencies from requirements.txt:
    ```bash
    pip3 install -r requirements.txt
    ```

4. Configure the miqro framework as shown below.

5. Run the software:
    ```bash
    python3 -m miqro_can
    ```

### Configuration

Define the MQTT broker by creating the file `/etc/miqro.yml`:

```yaml
broker:
    host: localhost
    port: 1883
    keepalive: 60
    
log_level: INFO

services:
  can:
    # Required: Device information for Home Assistant
    device:
      name: "My Vehicle"
      model: "Jumper"
      manufacturer: "Citroën"
      identifiers:
        - "vehicle-id-123"

    # Optional: CAN bus configuration
    can:
      channel: "can0"              # CAN interface (default: can0)
      timeout: 0.005               # Receive timeout in seconds (default: 0.005)
      speed_calibration_mult: 1.0  # Speed multiplier for calibration (default: 1.0)
      speed_calibration_add: 0     # Speed offset for calibration (default: 0)
```

The device section is required. Provide your vehicle information without leaking sensitive identifiers; use generic identifiers like a custom vehicle ID instead of VINs or license plates.

#### Optional: UDP Sender

To send the speed value via UDP to an ESPHome node (requires `esphome-udp-sender` package), add the following to your configuration:

```yaml
services:
  can:
    udp_sender:
      enabled: true
      addresses:
        - "dashboardesp.local"
      # Optional: encryption_key for secured communication
      # encryption_key: "my secret pass phrase"
      # Optional: rolling code for replay protection (requires encryption_key)
      # rolling_code: true
      # rolling_code_start: 0
```

The UDP sender will periodically send the current speed value to the configured ESPHome device(s). If the `esphome-udp-sender` package is not installed, the service will continue to work normally with UDP sending simply disabled.

### Home Assistant devices

The configured device (e.g. "Basisfahrzeug") holds the main values: ignition,
stationary, speed, odometer, fuel level and warning, range, voltage, outside
temperature, CAN bus activity. The other entities are grouped in child devices
(Home Assistant 2026.11+): *Türen & Schloss*, *Fahrbetrieb*, *Strecke & Verbrauch*,
*Wartung* and *CAN-Log*. They keep the unique IDs they had on the main device,
so entity IDs and history are unchanged. Other services can add their own
children to the same device by using one of its identifiers.

### MQTT Topics

The service publishes CAN bus messages to MQTT topics under `service/can/`.

#### Published Topics

| Data | MQTT Topic | Unit | Notes |
|------|-----------|------|-------|
| Door Status | `service/can/door/{front_left,front_right,side,back,any,all}` | binary | Door open/closed status |
| Handbrake | `service/can/handbrake` | binary | Handbrake engaged |
| Light | `service/can/light` | binary | Driving lights on/off |
| Pitch Angle | `service/can/attitude/pitch` | ° | Vehicle pitch angle |
| Roll Angle | `service/can/attitude/roll` | ° | Vehicle roll angle |
| Outside Temperature | `service/can/temperature/outside` | °C | Ambient temperature |
| Voltage | `service/can/voltage` | V | Battery voltage |
| Seatbelt | `service/can/seatbelt_open` | binary | Driver seatbelt status |
| Oil Change Maintenance | `service/can/maintenance/oil_change_km` | km | Distance until oil change |
| Unknown Maintenance | `service/can/maintenance/unknown_km` | km | Distance for unknown maintenance |
| Service Maintenance | `service/can/maintenance/service_km` | km | Distance until next service (interval unknown) |
| Speed | `service/can/speed` | km/h | Current vehicle speed |
| Traction Control Plus | `service/can/traction_plus` | binary | Traction control active |
| Fuel Consumption | `service/can/current_consumption` | l/100km | Current fuel consumption, never below 2.0 — see [below](#current-fuel-consumption) |
| Vehicle Stationary | `service/can/stationary` | binary | Vehicle standing still (below ~3.4 km/h), as signalled by the trip computer |
| Total Distance | `service/can/overall_kilometers` | km | Total kilometers driven |
| Fuel Range | `service/can/kilometers_remaining` | km | Kilometers remaining on fuel |
| Distance Today / Trip | `service/can/{today,trip}/distance` | km | From the wheel counters. The day starts at 3:00; a trip ends after 15 min of ignition off and is reset when the next one starts |
| Fuel Consumed Today / Trip | `service/can/{today,trip}/fuel_consumed` | L | Current consumption (l/100km) integrated over the distance; idling is not counted |
| Fuel Consumed by Gauge Today / Trip | `service/can/{today,trip}/fuel_consumed_gauge` | L | Drop of the fuel gauge since the start, corrected for refuelling (steps of 1 % of the 90 L tank) |
| Fuel Consumed Difference Today / Trip | `service/can/{today,trip}/fuel_consumed_difference` | L | Integrated minus gauge-based consumption |
| Front Door Lock | `service/can/lock/front` | locked/unlocked | Front doors lock status |
| Back Door Lock | `service/can/lock/back` | locked/unlocked | Back doors lock status |
| All Doors Locked | `service/can/lock/all_locked` | binary | All doors locked status |
| Lock Buttons | `service/can/lock/button/{close,front,back}` | press/release/long_press/double_press | Remote lock button presses |
| Cruise Control Status | `service/can/cruise_control/status` | 0-2 | Cruise control state |
| Cruise Control Button | `service/can/cruise_control/button_pressed` | binary | Cruise control button pressed |
| Start-Stop Disabled | `service/can/start_stop/disabled` | binary | Start-stop disabled |
| Reverse Gear | `service/can/reverse` | binary | Reverse gear engaged |
| Coolant Temperature | `service/can/coolant_temperature` | °C | Engine coolant temperature, ~1 Hz |
| Engine Speed | `service/can/engine_speed` | rpm | 32 rpm resolution, ~1 Hz |
| Accelerator Pedal | `service/can/accelerator_pedal` | % | Averaged over ~1 s; 100 % = raw `0xFF` (kickdown); flooring the pedal without kickdown reads ~99 % |
| CAN Bus Active | `service/can/active` | binary | Bus connectivity status |
| Ignition | `service/can/ignition` | binary | Ignition on/off |

#### Current Fuel Consumption

The instantaneous consumption comes from the trip computer frame `0x0C054003` and behaves exactly
like the value in the instrument cluster display:

* **While driving** it is the current consumption in l/100 km, but **never below 2.0**. A value of
  2.0 therefore means "2.0 or less" — it is shown during overrun fuel cut-off (true consumption 0)
  and often while rolling towards a junction at idle.
* **When the vehicle stands still** (below ~3.4 km/h; it resumes above ~5 km/h), the trip computer
  sets a stationary flag (byte 4, bit `0x80`) and freezes the consumption field at its last value
  from driving. The cluster ignores that stale value and displays **2.0**; this service does the
  same and publishes 2.0. The flag itself is published as `stationary`.
* **With ignition off** the cluster shows dashes. The frame keeps being sent for a while with the
  stationary flag set, so the service keeps publishing 2.0; use the `ignition` topic to tell the
  two cases apart.

Since l/100 km is undefined at standstill and 2.0 is a display floor rather than a measurement,
avoid averaging this sensor over time to estimate fuel use — use the fuel level or the range
instead.
