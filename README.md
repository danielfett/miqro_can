# miqro_can

This is a software implementation for reading CAN bus data from Citroen Jumper (tested in a 2020 model) and publishing it to an MQTT broker.

**WARNING: This software must not be used while driving. Use only when the vehicle is stationary.**

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

2. Install dependencies from requirements.txt:
    ```bash
    pip3 install -r requirements.txt
    ```

3. Configure the miqro framework as shown below.

3. Run the software:
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

services: {}
```

### MQTT Topics

The service publishes CAN bus messages to MQTT topics under `service/canbus/`.

#### Published Topics

| Data | MQTT Topic | Unit | Notes |
|------|-----------|------|-------|
| Door Status | `service/canbus/door/{front_left,front_right,side,back,any,all}` | binary | Door open/closed status |
| Handbrake | `service/canbus/handbrake` | binary | Handbrake engaged |
| Light | `service/canbus/light` | binary | Driving lights on/off |
| Pitch Angle | `service/canbus/attitude/pitch` | ° | Vehicle pitch angle |
| Roll Angle | `service/canbus/attitude/roll` | ° | Vehicle roll angle |
| Outside Temperature | `service/canbus/temperature/outside` | °C | Ambient temperature |
| Voltage | `service/canbus/voltage` | V | Battery voltage |
| Seatbelt | `service/canbus/seatbelt_open` | binary | Driver seatbelt status |
| Oil Change Maintenance | `service/canbus/maintenance/oil_change_km` | km | Distance until oil change |
| Unknown Maintenance | `service/canbus/maintenance/unknown_km` | km | Distance for unknown maintenance |
| Speed | `service/canbus/speed` | km/h | Current vehicle speed |
| Traction Control Plus | `service/canbus/traction_plus` | binary | Traction control active |
| Fuel Consumption | `service/canbus/current_consumption` | l/100km | Current fuel consumption |
| Total Distance | `service/canbus/overall_kilometers` | km | Total kilometers driven |
| Fuel Range | `service/canbus/kilometers_remaining` | km | Kilometers remaining on fuel |
| Front Door Lock | `service/canbus/lock/front` | locked/unlocked | Front doors lock status |
| Back Door Lock | `service/canbus/lock/back` | locked/unlocked | Back doors lock status |
| All Doors Locked | `service/canbus/lock/all_locked` | binary | All doors locked status |
| Lock Buttons | `service/canbus/lock/button/{close,front,back}` | press/release/long_press/double_press | Remote lock button presses |
| Cruise Control Status | `service/canbus/cruise_control/status` | 0-2 | Cruise control state |
| Cruise Control Button | `service/canbus/cruise_control/button_pressed` | binary | Cruise control button pressed |
| Start-Stop Disabled | `service/canbus/start_stop/disabled` | binary | Start-stop disabled |
| Reverse Gear | `service/canbus/reverse` | binary | Reverse gear engaged |
| CAN Bus Active | `service/canbus/active` | binary | Bus connectivity status |
| Ignition | `service/canbus/ignition` | binary | Ignition on/off |