import miqro
from miqro import ha_sensors
from datetime import datetime, timedelta
from can.interfaces.socketcan import SocketcanBus
from can import CanError
from collections import deque
import os
from time import sleep

try:
    from esphome_packet_transport import PacketTransport
    HAS_UDP_SENDER = True
except ImportError:
    HAS_UDP_SENDER = False


class CANService(miqro.Service):
    SERVICE_NAME = "can"
    CAN_CHANNEL = "can0"
    CAN_TIMEOUT = 0.005

    LOOP_INTERVAL = 0.02

    SPEED_CAL_ADD = 0
    SPEED_CAL_MULT = 1.0

    angle_pitch = deque(maxlen=10)
    angle_roll = deque(maxlen=10)
    speed = deque(maxlen=10)
    last_attitude_acquired = None
    last_ignition_signal_acquired = None
    last_lock_status = {"front": True, "back": True}  # True is locked
    debug = False
    distance_accumulation_enabled = False
    _last_wheel_counters = {}
    _distance_left_cm = 0
    _distance_right_cm = 0
    _last_engine_telemetry_published = None
    _recording_file = None
    _recording_path = None
    _recording_until = None

    CAN_LOG_DIR = "/var/lib/miqro/can_log"

    CAN_ATTITUDE_MAX_DELAY = timedelta(seconds=0.2)
    CAN_ACTIVITY_TIMEOUT = timedelta(seconds=3)
    IGNITION_TIMEOUT = timedelta(seconds=3)

    def create_ha_sensors(self):
        device_config = self.service_config.get("device", {})
        device_name = device_config.get("name", "Vehicle")
        device_model = device_config.get("model", "Unknown")
        device_manufacturer = device_config.get("manufacturer", "Unknown")
        device_identifiers = device_config.get("identifiers", [device_name])

        self.device_car = ha_sensors.Device(
            self,
            name=device_name,
            model=device_model,
            manufacturer=device_manufacturer,
            identifiers=device_identifiers,
        )

        self.sensor_door_front_left = ha_sensors.BinarySensor(
            self.device_car,
            name="Tür vorne links",
            state_topic_postfix="door/front_left",
            device_class="door",
        )
        self.sensor_door_front_right = ha_sensors.BinarySensor(
            self.device_car,
            name="Tür vorne rechts",
            state_topic_postfix="door/front_right",
            device_class="door",
        )
        self.sensor_door_side = ha_sensors.BinarySensor(
            self.device_car,
            name="Schiebetür",
            state_topic_postfix="door/side",
            device_class="door",
        )
        self.sensor_door_back = ha_sensors.BinarySensor(
            self.device_car,
            name="Hecktür",
            state_topic_postfix="door/back",
            device_class="door",
        )
        self.sensor_door_any = ha_sensors.BinarySensor(
            self.device_car,
            name="Irgendeine Tür",
            state_topic_postfix="door/any",
            device_class="door",
        )
        self.sensor_door_all = ha_sensors.BinarySensor(
            self.device_car,
            name="Alle Türen",
            state_topic_postfix="door/all",
            device_class="door",
        )
        self.sensor_handbrake = ha_sensors.BinarySensor(
            self.device_car,
            name="Handbremse",
            state_topic_postfix="handbrake",
            device_class="safety",
        )
        self.sensor_light = ha_sensors.BinarySensor(
            self.device_car,
            name="Fahrlicht",
            state_topic_postfix="light",
            device_class="light",
        )

        # attitude

        self.sensor_attitude_pitch = ha_sensors.Sensor(
            self.device_car,
            name="Nickwinkel",
            state_topic_postfix="attitude/pitch",
            unit_of_measurement="°",
            state_class="measurement",
        )
        self.sensor_attitude_roll = ha_sensors.Sensor(
            self.device_car,
            name="Rollwinkel",
            state_topic_postfix="attitude/roll",
            unit_of_measurement="°",
            state_class="measurement",
        )

        # temperature

        self.sensor_temperature_outside = ha_sensors.Sensor(
            self.device_car,
            name="Außentemperatur",
            state_topic_postfix="temperature/outside",
            unit_of_measurement="°C",
            device_class="temperature",
            state_class="measurement",
        )
        self.sensor_temperature_unknown = ha_sensors.Sensor(
            self.device_car,
            name="Temperatur (unbekannt)",
            state_topic_postfix="temperature/unknown",
            unit_of_measurement="°C",
            device_class="temperature",
            state_class="measurement",
        )
        self.sensor_coolant_temperature = ha_sensors.Sensor(
            self.device_car,
            name="Kühlmitteltemperatur",
            state_topic_postfix="coolant_temperature",
            unit_of_measurement="°C",
            device_class="temperature",
            state_class="measurement",
        )

        # voltage

        self.sensor_voltage = ha_sensors.Sensor(
            self.device_car,
            name="Spannung",
            state_topic_postfix="voltage",
            unit_of_measurement="V",
            device_class="voltage",
            state_class="measurement",
        )

        # seatbelt

        self.sensor_seatbelt_open = ha_sensors.BinarySensor(
            self.device_car,
            name="Sicherheitsgurt offen (Fahrersitz)",
            state_topic_postfix="seatbelt_open",
            device_class="safety",
        )

        # maintenance

        self.sensor_maintenance_oil_change_km = ha_sensors.Sensor(
            self.device_car,
            name="Wartung: Ölwechsel in km",
            state_topic_postfix="maintenance/oil_change_km",
            unit_of_measurement="km",
        )
        self.sensor_maintenance_unknown_km = ha_sensors.Sensor(
            self.device_car,
            name="Wartung: Unbekannt in km",
            state_topic_postfix="maintenance/unknown_km",
            unit_of_measurement="km",
        )
        self.sensor_maintenance_service_km = ha_sensors.Sensor(
            self.device_car,
            name="Wartung: Service in km",
            state_topic_postfix="maintenance/service_km",
            unit_of_measurement="km",
        )

        # speed

        self.sensor_speed = ha_sensors.Sensor(
            self.device_car,
            name="Geschwindigkeit",
            state_topic_postfix="speed",
            unit_of_measurement="km/h",
            device_class="speed",
            state_class="measurement",
        )

        self.sensor_traction_plus = ha_sensors.BinarySensor(
            self.device_car,
            name="Traktionskontrolle Plus aktiviert",
            state_topic_postfix="traction_plus",
            device_class="safety",
        )

        self.sensor_distance_left = ha_sensors.Sensor(
            self.device_car,
            name="Strecke links (akkumuliert)",
            state_topic_postfix="distance_left",
            unit_of_measurement="cm",
            state_class="total_increasing",
        )
        self.sensor_distance_right = ha_sensors.Sensor(
            self.device_car,
            name="Strecke rechts (akkumuliert)",
            state_topic_postfix="distance_right",
            unit_of_measurement="cm",
            state_class="total_increasing",
        )
        self.button_distance_accumulation = ha_sensors.Button(
            self.device_car,
            name="Streckenmessung starten",
            command_topic_postfix="distance_accumulation/start",
            command_callback=self.handle_distance_accumulation_button,
            icon="mdi:map-marker-distance",
        )
        self.sensor_distance_accumulation_active = ha_sensors.BinarySensor(
            self.device_car,
            name="Streckenmessung aktiv",
            state_topic_postfix="distance_accumulation/active",
            device_class="running",
        )

        # CAN bus recording

        self.button_can_log_10s = ha_sensors.Button(
            self.device_car,
            name="CAN-Log 10 Sekunden",
            command_topic_postfix="can_log/record_10s",
            command_callback=self.handle_can_log_10s,
            icon="mdi:record-rec",
        )
        self.button_can_log_60s = ha_sensors.Button(
            self.device_car,
            name="CAN-Log 60 Sekunden",
            command_topic_postfix="can_log/record_60s",
            command_callback=self.handle_can_log_60s,
            icon="mdi:record-rec",
        )
        self.button_can_log_5min = ha_sensors.Button(
            self.device_car,
            name="CAN-Log 5 Minuten",
            command_topic_postfix="can_log/record_5min",
            command_callback=self.handle_can_log_5min,
            icon="mdi:record-rec",
        )
        self.button_can_log_cancel = ha_sensors.Button(
            self.device_car,
            name="CAN-Log abbrechen",
            command_topic_postfix="can_log/cancel",
            command_callback=self.handle_can_log_cancel,
            icon="mdi:stop",
        )
        self.sensor_can_log_status = ha_sensors.Sensor(
            self.device_car,
            name="CAN-Log Status",
            state_topic_postfix="can_log/status",
            icon="mdi:file-document-outline",
        )
        self.sensor_can_log_file = ha_sensors.Sensor(
            self.device_car,
            name="CAN-Log Datei",
            state_topic_postfix="can_log/file",
            icon="mdi:file-outline",
        )

        # engine

        self.sensor_engine_speed = ha_sensors.Sensor(
            self.device_car,
            name="Drehzahl",
            state_topic_postfix="engine_speed",
            unit_of_measurement="rpm",
            state_class="measurement",
        )
        self.sensor_accelerator_pedal = ha_sensors.Sensor(
            self.device_car,
            name="Gaspedal",
            state_topic_postfix="accelerator_pedal",
            unit_of_measurement="%",
            state_class="measurement",
            icon="mdi:speedometer",
        )

        # fuel

        self.sensor_fuel_level = ha_sensors.Sensor(
            self.device_car,
            name="Kraftstoff",
            state_topic_postfix="fuel_level",
            unit_of_measurement="L",
            icon="mdi:fuel",
        )
        self.sensor_fuel_warning = ha_sensors.BinarySensor(
            self.device_car,
            name="Kraftstoff niedrig",
            state_topic_postfix="fuel_warning",
            device_class="problem",
        )

        self.sensor_current_consumption = ha_sensors.Sensor(
            self.device_car,
            name="Aktueller Verbrauch",
            state_topic_postfix="current_consumption",
            unit_of_measurement="l/100km",
            state_class="measurement",
        )
        self.sensor_stationary = ha_sensors.BinarySensor(
            self.device_car,
            name="Fahrzeug steht",
            state_topic_postfix="stationary",
        )
        self.sensor_overall_kilometers = ha_sensors.Sensor(
            self.device_car,
            name="Gesamt-Kilometerstand",
            state_topic_postfix="overall_kilometers",
            unit_of_measurement="km",
            device_class="distance",
            state_class="total_increasing",
        )
        self.sensor_kilometers_remaining = ha_sensors.Sensor(
            self.device_car,
            name="Reichweite",
            state_topic_postfix="kilometers_remaining",
            unit_of_measurement="km",
            device_class="distance",
            state_class="measurement",
        )

        # lock (front/back/all_locked)

        self.sensor_lock_front = ha_sensors.BinarySensor(
            self.device_car,
            name="Vordertüren Verriegelungsstatus",
            state_topic_postfix="lock/front",
            device_class="lock",
            payload_on="unlocked",
            payload_off="locked",
        )
        self.sensor_lock_back = ha_sensors.BinarySensor(
            self.device_car,
            name="Hecktüren Verriegelungsstatus",
            state_topic_postfix="lock/back",
            device_class="lock",
            payload_on="unlocked",
            payload_off="locked",
        )
        self.sensor_lock_all_locked = ha_sensors.BinarySensor(
            self.device_car,
            name="Alle Türen verriegelt",
            state_topic_postfix="lock/all_locked",
            device_class="lock",
            payload_on="0",
            payload_off="1",
        )

        # lock button events

        self.event_lock_button_close = ha_sensors.Event(
            self.device_car,
            name="Fernbedienung Schließ-Knopf",
            state_topic_postfix="lock/button/close",
            event_types=["press", "long_press", "double_press", "release"],
            value_template="""{"event_type": "{{ value }}"}""",
        )
        self.event_lock_button_front = ha_sensors.Event(
            self.device_car,
            name="Fernbedienung Vordertüren-Knopf",
            state_topic_postfix="lock/button/front",
            event_types=["press", "long_press", "double_press", "release"],
            value_template="""{"event_type": "{{ value }}"}""",
        )
        self.event_lock_button_back = ha_sensors.Event(
            self.device_car,
            name="Fernbedienung Hecktüren-Knopf",
            state_topic_postfix="lock/button/back",
            event_types=["press", "long_press", "double_press", "release"],
            value_template="""{"event_type": "{{ value }}"}""",
        )

        # cruise control

        self.sensor_cruise_control_status = ha_sensors.Sensor(
            self.device_car,
            name="Tempomat Status",
            state_topic_postfix="cruise_control/status",
        )
        self.sensor_cruise_control_button_pressed = ha_sensors.BinarySensor(
            self.device_car,
            name="Tempomat Knopf gedrückt",
            state_topic_postfix="cruise_control/button_pressed",
            device_class="safety",
        )

        # reverse

        self.sensor_reverse = ha_sensors.BinarySensor(
            self.device_car,
            name="Rückwärtsgang eingelegt",
            state_topic_postfix="reverse",
            device_class="safety",
        )

        # activity

        self.sensor_active = ha_sensors.BinarySensor(
            self.device_car,
            name="CAN-Bus aktiv",
            state_topic_postfix="active",
            device_class="connectivity",
        )
        self.sensor_ignition = ha_sensors.BinarySensor(
            self.device_car,
            name="Zündung",
            state_topic_postfix="ignition",
            device_class="power",
        )

    def handle_door_vehicle_status(self, msg):
        door_status_byte = msg.data[1]
        new_door_status = {
            "front_left": 1 if (door_status_byte & 0b00000100) else 0,
            "front_right": 1 if (door_status_byte & 0b00001000) else 0,
            "side": 1 if (door_status_byte & 0b00110000) else 0,
            "back": 1 if (door_status_byte & 0b01000000) else 0,
        }
        new_door_status["any"] = any(new_door_status.values())
        new_door_status["all"] = all(new_door_status.values())

        self.publish_json_keys(
            new_door_status,
            "door",
            retain=True,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=timedelta(seconds=20),
        )
        # handbrake is second bit in first nibble
        handbrake = 1 if (msg.data[0] & 0x20) else 0
        self.publish("handbrake", handbrake, only_if_changed=True, retain=True)

        # light is the fourth bit
        light = 1 if (msg.data[0] & 0x08) else 0
        self.publish("light", light, only_if_changed=True)

        # handle lock status from byte 3: 0x20 means locked, 0x00 means unlocked
        unknown = 1 if (msg.data[3] & 0x20) else 0
        self.publish("unknown_maybe_lock", unknown, only_if_changed=True)

        # fuel level in litres (byte 5)
        # When the car is parked/shut down, the cluster sends zeroed frames.
        # A real tank level of 0 L is not observable (the low-fuel warning kicks
        # in long before), so treat 0 as "no reading" and keep the last value.
        fuel_level = msg.data[5]
        if fuel_level:
            self.publish(
                "fuel_level",
                fuel_level,
                only_if_changed=timedelta(seconds=30),
                retain=True,
            )

            # low-fuel warning lamp (byte 4, bit 1) - only trustworthy in the
            # same frames that carry a valid fuel level
            fuel_warning = 1 if (msg.data[4] & 0x02) else 0
            self.publish(
                "fuel_warning",
                fuel_warning,
                only_if_changed=timedelta(seconds=30),
                retain=True,
            )

    def handle_attitude(self, msg):
        if not msg.data[0] and not msg.data[1]:
            return

        # accelerator pedal (byte 3): 0 = released, raw 0..255. Mapped linearly
        # to percent; whether 255 really is full throttle is not yet verified
        # (max. observed so far: 184). Averaged and published at ~1 Hz.
        self._pedal_samples.append(msg.data[3])
        now = datetime.now()
        if (
            self._last_pedal_published is None
            or now - self._last_pedal_published >= timedelta(seconds=1)
        ):
            pedal = sum(self._pedal_samples) / len(self._pedal_samples) / 255 * 100
            self.publish("accelerator_pedal", round(pedal, 1), only_if_changed=True)
            self._pedal_samples.clear()
            self._last_pedal_published = now

        self.angle_pitch.append(((msg.data[0] << 4 | msg.data[1] >> 4) - 2**11) / 10)
        self.angle_roll.append((((msg.data[1] & 0xF) << 8 | msg.data[2]) - 2**11) / 10)
        self.last_attitude_acquired = datetime.now()

    def handle_temperature(self, msg):
        temp = (msg.data[0] - 80) / 2
        self.publish("temperature/outside", temp, only_if_changed=True)

        temp_unknown = (msg.data[5] - 80) / 2
        self.publish("temperature/unknown", temp_unknown, only_if_changed=True)

        voltage = msg.data[1] / 6
        self.publish("voltage", voltage, only_if_changed=True)

    def handle_seatbelt(self, msg):
        seatbelt_status = int(msg.data[2])
        self.publish(
            "seatbelt_open",
            seatbelt_status,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=timedelta(seconds=5),
            retain=True,
        )

    def handle_maintenance(self, msg):
        if not (msg.data[1] == 0xFF and msg.data[2] == 0xFF):
            oil = msg.data[1] << 8 | msg.data[2]
            self.publish(
                "maintenance/oil_change_km",
                oil,
                qos=self.QOS_AT_LEAST_ONCE,
                only_if_changed=True,
                retain=True,
            )

        if not (msg.data[4] == 0xFF and (msg.data[5] & 0xF0) == 0xF0):
            unknown = (msg.data[4] << 4 | msg.data[5] >> 4) * 16
            self.publish(
                "maintenance/unknown_km",
                unknown,
                qos=self.QOS_AT_LEAST_ONCE,
                only_if_changed=True,
                retain=True,
            )

    def handle_service_countdown(self, msg):
        # 0x0A194003: second maintenance countdown, alongside the oil change in
        # 0x06254000. 16-bit big-endian, 2 km per count. Which service interval
        # it tracks is not known, so the name is kept neutral.
        # Bytes 2-7 are always zero.
        service_km = ((msg.data[0] << 8) | msg.data[1]) * 2
        self.publish(
            "maintenance/service_km",
            service_km,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=True,
            retain=True,
        )

    def handle_speed(self, msg):
        # 2023-11: there seem to be other data in the first nibble of msg.data[2], so we treat it separately
        first_nibble = msg.data[2] >> 4
        second_nibble = msg.data[2] & 0xF

        # first_nibble[1] is whether traction plus is activated
        traction_plus = first_nibble & 0x4
        self.publish(
            "traction_plus",
            traction_plus,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=True,
        )

        # rest is the speed in 16ths of km/h
        speed = (
            round(int(second_nibble << 8 | msg.data[3]) / 16) * self.speed_cal_mult
            + self.speed_cal_add
        )

        self.send_speed_via_udp(speed)

        self.speed.append(speed)
        self.publish("speed", speed, qos=self.QOS_AT_LEAST_ONCE)

        # distance counters (bytes 4-7): two 13-bit wheel accumulators (left and right)
        if self.distance_accumulation_enabled:
            c_left = (msg.data[4] << 8 | msg.data[5]) & 0x1FFF
            c_right = (msg.data[6] << 8 | msg.data[7]) & 0x1FFF

            # left wheel
            prev_left = self._last_wheel_counters.get("left")
            self._last_wheel_counters["left"] = c_left
            if prev_left is not None:
                delta_counts = (c_left - prev_left) & 0x1FFF
                delta_m = delta_counts / 43.2
                self._distance_left_cm += delta_m * 100
                self.publish(
                    "distance_left",
                    int(self._distance_left_cm),
                    qos=self.QOS_AT_LEAST_ONCE,
                )

            # right wheel
            prev_right = self._last_wheel_counters.get("right")
            self._last_wheel_counters["right"] = c_right
            if prev_right is not None:
                delta_counts = (c_right - prev_right) & 0x1FFF
                delta_m = delta_counts / 43.2
                self._distance_right_cm += delta_m * 100
                self.publish(
                    "distance_right",
                    int(self._distance_right_cm),
                    qos=self.QOS_AT_LEAST_ONCE,
                )

        self.last_ignition_signal_acquired = datetime.now()

    CONSUMPTION_FLOOR = 2.0

    def handle_fuel(self, msg):
        # byte 4 bit 0x80: vehicle stationary (set below ~3.4 km/h, cleared
        # above ~5 km/h). While set, the consumption field is frozen at its last
        # value from driving; the instrument cluster ignores it and displays the
        # floor value 2.0 instead (dashes with ignition off). Mirror the display.
        stationary = 1 if (msg.data[4] & 0x80) else 0
        self.publish("stationary", stationary, only_if_changed=True)

        # first three nibbles represent current fuel consumption, BCD. Never
        # below 2.0 - that value also covers overrun fuel cut-off (true 0).
        if stationary:
            consumption = self.CONSUMPTION_FLOOR
        else:
            consumption = float("%x.%x" % (msg.data[0], msg.data[1] >> 4))
        self.publish("current_consumption", consumption, qos=self.QOS_AT_LEAST_ONCE)

        # next five nibbles are overall kilometers
        overall_kilometers = int(
            (msg.data[1] & 0xF) << 16 | msg.data[2] << 8 | msg.data[3]
        )
        self.publish(
            "overall_kilometers",
            overall_kilometers,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=True,
            retain=True,
        )

        # kilometers remaining is three nibbles
        kilometers_remaining = ((msg.data[4] & 0xF) << 8) | msg.data[5]
        self.publish(
            "kilometers_remaining",
            kilometers_remaining,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=True,
            retain=True,
        )

    def handle_lock(self, msg):
        # msg.data        0  1  2  3  4  5  6  7
        # no change:     00 00 00 00 00 00 00 00
        # open back:     00 00 00 00 00 00 80 00
        # open front:    00 00 00 00 00 08 00 00
        # lock all:      00 00 00 00 00 80 00 00
        #                               ^^ ^

        if msg.data[5] & 0x80:
            self.last_lock_status["front"] = True
            self.last_lock_status["back"] = True
        if msg.data[5] & 0x08:
            self.last_lock_status["front"] = False
        if msg.data[6] & 0x80:
            self.last_lock_status["back"] = False
        data = {
            "front": "locked" if self.last_lock_status["front"] else "unlocked",
            "back": "locked" if self.last_lock_status["back"] else "unlocked",
            "all_locked": self.last_lock_status["front"]
            and self.last_lock_status["back"],
        }
        self.publish_json("lock/data", data, only_if_changed=True)
        self.publish_json_keys(
            data,
            "lock",
            retain=True,
            qos=self.QOS_EXACTLY_ONCE,
            only_if_changed=timedelta(minutes=1),
        )

        # if the value is "2" this means long_press, 4 means double_press, 1 means release
        # create button-specific messages as well if any of these apply
        self.button_to_msg("close", msg.data[5])
        self.button_to_msg("front", msg.data[5] << 4)
        self.button_to_msg("back", msg.data[6])

    def button_to_msg(self, button, data):
        if data & 0x20:
            self.publish(f"lock/button/{button}", "long_press", only_if_changed=False)
        elif data & 0x40:
            self.publish(f"lock/button/{button}", "double_press", only_if_changed=False)
        elif data & 0x10:
            self.publish(f"lock/button/{button}", "release", only_if_changed=False)
        elif data & 0x80:
            self.publish(f"lock/button/{button}", "press", only_if_changed=False)

    def handle_cruise_control(self, msg):
        if msg.data[0] & 0x80:
            status = 1
        elif msg.data[0] & 0x40:
            status = 2
        else:
            status = 0

        button_pressed = 1 if (msg.data[0] & 0x08) else 0

        self.publish_json_keys(
            {
                "status": status,
                "button_pressed": button_pressed,
            },
            "cruise_control",
            retain=True,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=timedelta(seconds=5),
        )

    def handle_start_stop(self, msg):
        disabled = 1 if (msg.data[6] == 0x04) else 0
        self.publish_json_keys(
            {
                "disabled": disabled,
            },
            "start_stop",
        )
        self.publish("raw/startstop", msg.data.hex(), only_if_changed=True)

    def handle_reverse(self, msg):
        # last nibble is 4 if reverse gear is engaged, 0 otherwise
        reverse = 1 if (msg.data[7] == 0x04) else 0
        self.publish("reverse", reverse, only_if_changed=timedelta(seconds=5))

        # coolant temperature (byte 3): °C = value - 40
        # (verified on a cold start: 15 °C at 9.5 °C outside, rising steadily)
        coolant_temp = msg.data[3] - 40
        # engine speed (byte 6): rpm = value * 32
        # (cold idle ~830 rpm, warm idle ~736 rpm)
        engine_speed = msg.data[6] * 32

        # throttle to ~1 Hz
        now = datetime.now()
        if (
            self._last_engine_telemetry_published is None
            or now - self._last_engine_telemetry_published >= timedelta(seconds=1)
        ):
            self.publish(
                "coolant_temperature",
                coolant_temp,
                only_if_changed=True,
            )
            self.publish(
                "engine_speed",
                engine_speed,
                only_if_changed=True,
            )
            self._last_engine_telemetry_published = now

    CAN_IDS = {
        handle_speed: {
            "can_id": 0x04214006,
            "can_mask": 0x1FFFFFFF,
        },
        handle_lock: {
            "can_id": 0x02294000,
            "can_mask": 0x1FFFFFFF,
        },
        handle_attitude: {
            "can_id": 0x04394100,
            "can_mask": 0x1FFFFFFF,
        },
        handle_door_vehicle_status: {
            "can_id": 0x06214000,
            "can_mask": 0x1FFFFFFF,
        },
        handle_seatbelt: {
            "can_id": 0x0621401A,
            "can_mask": 0x1FFFFFFF,
        },
        handle_maintenance: {
            "can_id": 0x06254000,
            "can_mask": 0x1FFFFFFF,
        },
        handle_service_countdown: {
            "can_id": 0x0A194003,
            "can_mask": 0x1FFFFFFF,
        },
        handle_start_stop: {
            "can_id": 0x06314000,
            "can_mask": 0x1FFFFFFF,
        },
        handle_temperature: {
            "can_id": 0x063D4000,
            "can_mask": 0x1FFFFFFF,
        },
        handle_fuel: {
            "can_id": 0x0C054003,
            "can_mask": 0x1FFFFFFF,
        },
        handle_cruise_control: {
            "can_id": 0x08014000,
            "can_mask": 0x1FFFFFFF,
        },
        handle_reverse: {
            "can_id": 0x04214001,
            "can_mask": 0x1FFFFFFF,
        },
    }

    def __init__(self, *args, can_cls=SocketcanBus, **kwargs):
        super().__init__(*args, **kwargs)

        can_config = self.service_config.get("can", {})
        self.can_channel = can_config.get("channel", self.CAN_CHANNEL)
        self.can_timeout = can_config.get("timeout", self.CAN_TIMEOUT)
        self.speed_cal_add = can_config.get("speed_calibration_add", self.SPEED_CAL_ADD)
        self.speed_cal_mult = can_config.get("speed_calibration_mult", self.SPEED_CAL_MULT)
        self.can_log_dir = can_config.get("log_dir", self.CAN_LOG_DIR)

        self._can_filters = list(self.CAN_IDS.values())
        self.bus = can_cls(self.can_channel, can_filters=self._can_filters)
        self.last_canbus_message_received = None
        self._pedal_samples = []
        self._last_pedal_published = None
        self.create_ha_sensors()
        self._init_udp_sender()

    def _init_udp_sender(self):
        self.udp_sender = None
        self.udp_sender_enabled = False

        if not HAS_UDP_SENDER:
            return

        udp_config = self.service_config.get("udp_sender", {})
        if not udp_config.get("enabled", False):
            return

        try:
            addresses = udp_config.get("addresses")
            encryption_key = udp_config.get("encryption_key")
            rolling_code = udp_config.get("rolling_code", False)
            rolling_code_start = udp_config.get("rolling_code_start", 0)

            kwargs = {"addresses": addresses} if addresses else {}
            if encryption_key:
                kwargs["encryption_key"] = encryption_key
            if rolling_code:
                kwargs["rolling_code"] = True
                kwargs["rolling_code_start"] = rolling_code_start

            self.udp_sender = PacketTransport("miqro_can", **kwargs)
            self.udp_sender_enabled = True
            self.log.info(f"UDP sender initialized with addresses: {addresses}")
        except Exception as e:
            self.log.error(f"Failed to initialize UDP sender: {e}")

    def send_speed_via_udp(self, speed):
        if not self.udp_sender_enabled or speed is None:
            return

        try:
            self.udp_sender.send(sensors={"speed_udp": speed})
        except Exception as e:
            self.log.error(f"Failed to send speed via UDP: {e}")

    def try_recover(self):
        # ifdown can0 and ifup can0 when the CAN bus is not working
        os.system(f"ip link set {self.can_channel} down")
        sleep(1)
        os.system(f"ip link set {self.can_channel} up type can bitrate 50000")

    @miqro.loop(seconds=0)
    def recv_from_can(self):
        if self._recording_file is not None and datetime.now() >= self._recording_until:
            self.stop_can_recording()

        try:
            msg = self.bus.recv(timeout=self.can_timeout)
        except CanError:
            self.try_recover()
            return

        if msg is None:
            return
        self.last_canbus_message_received = datetime.now()

        if self._recording_file is not None:
            self._record_can_message(msg)

        for fn, can_filter in self.CAN_IDS.items():
            if (msg.arbitration_id & can_filter["can_mask"]) == can_filter["can_id"]:
                fn(self, msg)
                if self.debug:
                    self.publish(
                        "raw/" + fn.__name__[7:], msg.data.hex(), only_if_changed=True
                    )
                break
        else:
            self.log.debug(f"Unknown id: {hex(msg.arbitration_id)}")
            if self.debug:
                self.publish(
                    f"raw/unknown/{hex(msg.arbitration_id)}",
                    msg.data.hex(),
                    only_if_changed=True,
                )

    @miqro.loop(seconds=0.2)
    def publish_attitude(self):
        if not len(self.angle_pitch) or not len(self.angle_roll):
            return
        if not self.last_attitude_acquired or (
            self.last_attitude_acquired < datetime.now() - self.CAN_ATTITUDE_MAX_DELAY
        ):
            self.angle_pitch.clear()
            self.angle_roll.clear()
            return

        attitude = {
            "pitch": sum(self.angle_pitch) / len(self.angle_pitch),
            "roll": sum(self.angle_roll) / len(self.angle_roll),
        }
        self.publish_json_keys(attitude, "attitude", only_if_changed=True)

    @miqro.loop(seconds=2)
    def publish_slow(self):
        if not len(self.angle_pitch) or not len(self.angle_roll) or not len(self.speed):
            return

        attitude = {
            "pitch": sum(self.angle_pitch) / len(self.angle_pitch),
            "roll": sum(self.angle_roll) / len(self.angle_roll),
        }
        self.publish_json_keys(attitude, "slow/attitude", only_if_changed=False)
        self.publish(
            "slow/speed", sum(self.speed) / len(self.speed), only_if_changed=False
        )

    @miqro.loop(seconds=1)
    def publish_activity(self):
        if (
            self.last_canbus_message_received is None
            or self.last_canbus_message_received
            < (datetime.now() - self.CAN_ACTIVITY_TIMEOUT)
        ):
            active = 0
        else:
            active = 1
        self.publish("active", active, only_if_changed=timedelta(seconds=60))

        if (
            self.last_ignition_signal_acquired is None
            or self.last_ignition_signal_acquired
            < (datetime.now() - self.IGNITION_TIMEOUT)
        ):
            ignition = 0
        else:
            ignition = 1
        self.publish("ignition", ignition, only_if_changed=timedelta(seconds=60))

        # auto-disable distance accumulation if CAN is unavailable for >1 minute
        if self.distance_accumulation_enabled and (
            self.last_canbus_message_received is None
            or self.last_canbus_message_received
            < (datetime.now() - timedelta(seconds=60))
        ):
            self.set_distance_accumulation(False)

        if self._recording_file is not None:
            self._recording_file.flush()
        self.publish_can_log_status()

    @miqro.handle("debug")
    def handle_debug(self, message):
        self.debug = message in ["1", "true", "on"]

    def set_distance_accumulation(self, enabled):
        if enabled:
            self.distance_accumulation_enabled = True
            self._distance_left_cm = 0
            self._distance_right_cm = 0
            self._last_wheel_counters = {}
        else:
            self.distance_accumulation_enabled = False
        self.publish(
            "distance_accumulation/active",
            self.distance_accumulation_enabled,
            retain=True,
            only_if_changed=True,
        )

    @miqro.handle("distance_accumulation")
    def handle_distance_accumulation(self, message):
        self.set_distance_accumulation(message in ["1", "true", "on"])

    def handle_distance_accumulation_button(self, _, payload):
        self.set_distance_accumulation(True)

    # CAN bus recording

    def handle_can_log_10s(self, _, payload):
        self.start_can_recording(10)

    def handle_can_log_60s(self, _, payload):
        self.start_can_recording(60)

    def handle_can_log_5min(self, _, payload):
        self.start_can_recording(300)

    def handle_can_log_cancel(self, _, payload):
        self.stop_can_recording()

    def start_can_recording(self, duration_seconds):
        if self._recording_file is not None:
            self._close_recording_file()

        try:
            os.makedirs(self.can_log_dir, exist_ok=True)
            base = datetime.now().strftime("can_%Y-%m-%d_%H-%M-%S")
            path = os.path.join(self.can_log_dir, f"{base}.log")
            counter = 1
            while os.path.exists(path):
                path = os.path.join(self.can_log_dir, f"{base}_{counter}.log")
                counter += 1
            self._recording_file = open(path, "w")
        except OSError as e:
            self.log.error(f"Cannot open CAN log file: {e}")
            self.publish("can_log/status", "error", retain=True, only_if_changed=True)
            return

        self._recording_path = path
        self._recording_until = datetime.now() + timedelta(seconds=duration_seconds)
        # record everything on the bus, not just the IDs handled above
        self.bus.set_filters(None)
        self.log.info(f"Recording CAN bus to {path} for {duration_seconds} s")
        self.publish("can_log/file", path, retain=True)
        self.publish_can_log_status()

    def stop_can_recording(self):
        if self._recording_file is None:
            return
        self._close_recording_file()
        self.publish_can_log_status()

    def _close_recording_file(self):
        self.bus.set_filters(self._can_filters)
        self._recording_file.close()
        self._recording_file = None
        self._recording_until = None
        self.log.info(f"CAN recording written to {self._recording_path}")

    def _record_can_message(self, msg):
        # candump-compatible log format, replayable with canplayer
        timestamp = msg.timestamp or datetime.now().timestamp()
        can_id = (
            f"{msg.arbitration_id:08X}"
            if msg.is_extended_id
            else f"{msg.arbitration_id:03X}"
        )
        self._recording_file.write(
            f"({timestamp:.6f}) {self.can_channel} {can_id}#{msg.data.hex().upper()}\n"
        )

    def publish_can_log_status(self):
        if self._recording_file is not None:
            remaining = (self._recording_until - datetime.now()).total_seconds()
            status = f"recording ({max(0, round(remaining))} s left)"
        else:
            status = "idle"
        self.publish("can_log/status", status, retain=True, only_if_changed=True)


if __name__ == "__main__":
    miqro.run(CANService)
