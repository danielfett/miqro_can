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


class ConsumptionPeriod:
    """Distance and fuel accumulated over one period (a day or a trip).

    Fuel is tracked twice so the two can be compared:

      * integrated: the instantaneous consumption (l/100km) integrated over the
        distance driven,
      * gauge: the drop of the fuel gauge (1 % steps of the tank, ~0.9 L)
        since the period started, corrected for refuelling.

    Refuelling shows up as the gauge rising by at least REFUEL_THRESHOLD_L
    above the lowest level seen. The level before refuelling closes the current
    segment; a new segment starts from the refuelled level. The cluster may
    ramp the gauge up slowly after refuelling, so for a while after a refuel the
    segment start follows the gauge upwards.
    """

    REFUEL_THRESHOLD_L = 5
    REFUEL_SETTLE_TIME = timedelta(minutes=5)
    # bumped when the unit of the gauge values changes; saved gauge values of
    # another version are discarded
    GAUGE_VERSION = 2

    def __init__(self, key, data=None):
        data = data or {}
        self.key = key
        self.distance_m = data.get("distance_m", 0.0)
        self.fuel_integrated_l = data.get("fuel_integrated_l", 0.0)
        if data.get("gauge_version") != self.GAUGE_VERSION:
            data = {}
        self.gauge_closed_l = data.get("gauge_closed_l", 0)
        self.gauge_segment_start_l = data.get("gauge_segment_start_l")
        self.gauge_segment_low_l = data.get("gauge_segment_low_l")
        self.gauge_current_l = data.get("gauge_current_l")
        self.refuelled_at = data.get("refuelled_at")  # timestamp or None

    def to_dict(self):
        return {
            "key": self.key,
            "distance_m": self.distance_m,
            "fuel_integrated_l": self.fuel_integrated_l,
            "gauge_closed_l": self.gauge_closed_l,
            "gauge_segment_start_l": self.gauge_segment_start_l,
            "gauge_segment_low_l": self.gauge_segment_low_l,
            "gauge_current_l": self.gauge_current_l,
            "refuelled_at": self.refuelled_at,
            "gauge_version": self.GAUGE_VERSION,
        }

    def add_distance(self, distance_m, consumption_l_per_100km):
        self.distance_m += distance_m
        if consumption_l_per_100km is not None:
            self.fuel_integrated_l += (
                distance_m / 1000 * consumption_l_per_100km / 100
            )

    def update_fuel_gauge(self, level_l, now):
        if self.gauge_segment_start_l is None:
            self.gauge_segment_start_l = level_l
            self.gauge_segment_low_l = level_l
        elif level_l >= self.gauge_segment_low_l + self.REFUEL_THRESHOLD_L:
            self.gauge_closed_l += self.gauge_segment_start_l - self.gauge_segment_low_l
            self.gauge_segment_start_l = level_l
            self.gauge_segment_low_l = level_l
            self.refuelled_at = now.timestamp()
        else:
            if (
                self.refuelled_at is not None
                and now.timestamp() - self.refuelled_at
                < self.REFUEL_SETTLE_TIME.total_seconds()
                and level_l > self.gauge_segment_start_l
            ):
                self.gauge_segment_start_l = level_l
                self.gauge_segment_low_l = level_l
            self.gauge_segment_low_l = min(self.gauge_segment_low_l, level_l)
        self.gauge_current_l = level_l

    @property
    def fuel_gauge_l(self):
        if self.gauge_segment_start_l is None:
            return None
        # + 0.0 turns a rounded -0.0 into 0.0
        return round(
            self.gauge_closed_l + self.gauge_segment_start_l - self.gauge_current_l,
            1,
        ) + 0.0

    def values(self):
        values = {
            "distance": round(self.distance_m / 1000, 2),
            "fuel_consumed": round(self.fuel_integrated_l, 2),
        }
        fuel_gauge = self.fuel_gauge_l
        if fuel_gauge is not None:
            values["fuel_consumed_gauge"] = fuel_gauge
            # + 0.0 turns a rounded -0.0 into 0.0
            values["fuel_consumed_difference"] = (
                round(self.fuel_integrated_l - fuel_gauge, 2) + 0.0
            )
        return values


class ParkingDetector:
    """Guesses whether the vehicle is being parked or manoeuvred right now
    (pulling onto a pitch, levelling with wedges, leaving it again), as
    opposed to driving on the road or being parked with the ignition off.

    With the ignition on it turns on
    - as soon as reverse gear is engaged,
    - when the vehicle is slow (<= slow_kmh) and has not been fast
      (> fast_kmh) for settle_time, or not at all since the ignition came on
      (pulling out of a parking spot);
    and turns off again above fast_kmh or when the ignition goes off. Between
    the two speeds the state is kept, so manoeuvring at walking pace does not
    flicker. A long stop at traffic lights or in a jam can look like parking;
    that is the price of not needing a map.
    """

    def __init__(self, slow_kmh=10, fast_kmh=25, settle_time=timedelta(seconds=60)):
        self.slow_kmh = slow_kmh
        self.fast_kmh = fast_kmh
        self.settle_time = settle_time
        self.state = False
        self.last_fast = None

    def update(self, now, ignition, speed_kmh, reverse):
        if not ignition:
            self.state = False
            self.last_fast = None
            return self.state
        speed = speed_kmh or 0.0  # no speed frames: standing
        if speed > self.fast_kmh:
            self.last_fast = now
            self.state = False
        elif reverse:
            self.state = True
        elif speed <= self.slow_kmh and (
            self.last_fast is None or now - self.last_fast >= self.settle_time
        ):
            self.state = True
        return self.state


class CANService(miqro.Service):
    SERVICE_NAME = "can"
    USE_STATE_FILE = True
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
    SPEED_MAX_AGE = timedelta(seconds=3)
    REVERSE_MAX_AGE = timedelta(seconds=3)

    # wheel counters are 13 bit (~190 m); a longer gap between speed frames
    # could hide a wrap-around, so the delta is discarded then
    WHEEL_COUNTER_MAX_GAP = timedelta(seconds=1)
    WHEEL_COUNTS_PER_M = 43.2
    CONSUMPTION_MAX_AGE = timedelta(seconds=10)
    FUEL_TANK_CAPACITY_L = 90  # Citroen Jumper 2020
    DAY_START_HOUR = 3
    TRIP_IGNITION_OFF_RESET = timedelta(minutes=15)
    PERIOD_STATE_SAVE_INTERVAL = timedelta(seconds=60)

    _odometry_last_counters = None
    _odometry_last_time = None
    _current_consumption = None
    _current_consumption_time = None
    _fuel_level_l = None
    _period_state_saved = None

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
        # Parts shown as child devices in Home Assistant (2026.11+). The
        # entities were on the main device before and keep their unique IDs
        # (and entity IDs) from there. Home Assistant puts the child's name in
        # front of the entity names.
        def child(name):
            return ha_sensors.ChildDevice(
                self.device_car, name=name, keep_parent_unique_ids=True
            )

        self.ha_doors = child("Türen & Schloss")
        self.ha_driving = child("Fahrbetrieb")
        self.ha_trip = child("Strecke & Verbrauch")
        self.ha_maintenance = child("Wartung")
        self.ha_can_log = child("CAN-Log")

        self.sensor_door_front_left = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Tür vorne links",
            state_topic_postfix="door/front_left",
            device_class="door",
        )
        self.sensor_door_front_right = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Tür vorne rechts",
            state_topic_postfix="door/front_right",
            device_class="door",
        )
        self.sensor_door_side = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Schiebetür",
            state_topic_postfix="door/side",
            device_class="door",
        )
        self.sensor_door_back = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Hecktür",
            state_topic_postfix="door/back",
            device_class="door",
        )
        self.sensor_door_any = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Irgendeine Tür",
            state_topic_postfix="door/any",
            device_class="door",
        )
        self.sensor_door_all = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Alle Türen",
            state_topic_postfix="door/all",
            device_class="door",
        )
        self.sensor_handbrake = ha_sensors.BinarySensor(
            self.ha_driving,
            name="Handbremse",
            state_topic_postfix="handbrake",
            device_class="safety",
        )
        self.sensor_light = ha_sensors.BinarySensor(
            self.ha_driving,
            name="Fahrlicht",
            state_topic_postfix="light",
            device_class="light",
        )

        # attitude

        self.sensor_attitude_pitch = ha_sensors.Sensor(
            self.ha_driving,
            name="Nickwinkel",
            state_topic_postfix="attitude/pitch",
            unit_of_measurement="°",
            state_class="measurement",
        )
        self.sensor_attitude_roll = ha_sensors.Sensor(
            self.ha_driving,
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
            self.ha_driving,
            name="Temperatur (unbekannt)",
            state_topic_postfix="temperature/unknown",
            unit_of_measurement="°C",
            device_class="temperature",
            state_class="measurement",
        )
        self.sensor_coolant_temperature = ha_sensors.Sensor(
            self.ha_driving,
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
            self.ha_driving,
            name="Sicherheitsgurt offen (Fahrersitz)",
            state_topic_postfix="seatbelt_open",
            device_class="safety",
        )

        # maintenance

        self.sensor_maintenance_oil_change_km = ha_sensors.Sensor(
            self.ha_maintenance,
            name="Ölwechsel fällig in",
            # was "Wartung: Ölwechsel in km" on the main device; keeps unique ID and entity ID
            default_entity_id="sensor.Wartung---lwechsel-in-km",
            state_topic_postfix="maintenance/oil_change_km",
            unit_of_measurement="km",
        )
        self.sensor_maintenance_unknown_km = ha_sensors.Sensor(
            self.ha_maintenance,
            name="Unbekannt fällig in",
            # was "Wartung: Unbekannt in km" on the main device; keeps unique ID and entity ID
            default_entity_id="sensor.Wartung--Unbekannt-in-km",
            state_topic_postfix="maintenance/unknown_km",
            unit_of_measurement="km",
        )
        self.sensor_maintenance_service_km = ha_sensors.Sensor(
            self.ha_maintenance,
            name="Service fällig in",
            # was "Wartung: Service in km" on the main device; keeps unique ID and entity ID
            default_entity_id="sensor.Wartung--Service-in-km",
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
            self.ha_driving,
            name="Traktionskontrolle Plus aktiviert",
            state_topic_postfix="traction_plus",
            device_class="safety",
        )

        self.sensor_distance_left = ha_sensors.Sensor(
            self.ha_trip,
            name="Strecke links (akkumuliert)",
            entity_category="diagnostic",
            state_topic_postfix="distance_left",
            unit_of_measurement="cm",
            state_class="total_increasing",
        )
        self.sensor_distance_right = ha_sensors.Sensor(
            self.ha_trip,
            name="Strecke rechts (akkumuliert)",
            entity_category="diagnostic",
            state_topic_postfix="distance_right",
            unit_of_measurement="cm",
            state_class="total_increasing",
        )
        self.button_distance_accumulation = ha_sensors.Button(
            self.ha_trip,
            name="Streckenmessung starten",
            command_topic_postfix="distance_accumulation/start",
            command_callback=self.handle_distance_accumulation_button,
            icon="mdi:map-marker-distance",
        )
        self.sensor_distance_accumulation_active = ha_sensors.BinarySensor(
            self.ha_trip,
            name="Streckenmessung aktiv",
            state_topic_postfix="distance_accumulation/active",
            device_class="running",
        )

        # CAN bus recording

        self.button_can_log_10s = ha_sensors.Button(
            self.ha_can_log,
            name="10 Sekunden aufzeichnen",
            # was "CAN-Log 10 Sekunden" on the main device; keeps unique ID and entity ID
            default_entity_id="button.CAN-Log-10-Sekunden",
            command_topic_postfix="can_log/record_10s",
            command_callback=self.handle_can_log_10s,
            icon="mdi:record-rec",
        )
        self.button_can_log_60s = ha_sensors.Button(
            self.ha_can_log,
            name="60 Sekunden aufzeichnen",
            # was "CAN-Log 60 Sekunden" on the main device; keeps unique ID and entity ID
            default_entity_id="button.CAN-Log-60-Sekunden",
            command_topic_postfix="can_log/record_60s",
            command_callback=self.handle_can_log_60s,
            icon="mdi:record-rec",
        )
        self.button_can_log_5min = ha_sensors.Button(
            self.ha_can_log,
            name="5 Minuten aufzeichnen",
            # was "CAN-Log 5 Minuten" on the main device; keeps unique ID and entity ID
            default_entity_id="button.CAN-Log-5-Minuten",
            command_topic_postfix="can_log/record_5min",
            command_callback=self.handle_can_log_5min,
            icon="mdi:record-rec",
        )
        self.button_can_log_cancel = ha_sensors.Button(
            self.ha_can_log,
            name="Abbrechen",
            # was "CAN-Log abbrechen" on the main device; keeps unique ID and entity ID
            default_entity_id="button.CAN-Log-abbrechen",
            command_topic_postfix="can_log/cancel",
            command_callback=self.handle_can_log_cancel,
            icon="mdi:stop",
        )
        self.sensor_can_log_status = ha_sensors.Sensor(
            self.ha_can_log,
            name="Status",
            # was "CAN-Log Status" on the main device; keeps unique ID and entity ID
            default_entity_id="sensor.CAN-Log-Status",
            state_topic_postfix="can_log/status",
            icon="mdi:file-document-outline",
        )
        self.sensor_can_log_file = ha_sensors.Sensor(
            self.ha_can_log,
            name="Datei",
            # was "CAN-Log Datei" on the main device; keeps unique ID and entity ID
            default_entity_id="sensor.CAN-Log-Datei",
            state_topic_postfix="can_log/file",
            icon="mdi:file-outline",
        )

        # engine

        self.sensor_engine_speed = ha_sensors.Sensor(
            self.ha_driving,
            name="Drehzahl",
            state_topic_postfix="engine_speed",
            unit_of_measurement="rpm",
            state_class="measurement",
        )
        self.sensor_accelerator_pedal = ha_sensors.Sensor(
            self.ha_driving,
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
            self.ha_trip,
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

        # calculated per day (from 3:00) and per trip

        for period, label in (("today", "heute"), ("trip", "Fahrt")):
            setattr(self, f"sensor_{period}_distance", ha_sensors.Sensor(
                self.ha_trip,
                name=f"Strecke {label}",
                state_topic_postfix=f"{period}/distance",
                unit_of_measurement="km",
                device_class="distance",
                state_class="total_increasing",
                suggested_display_precision=1,
            ))
            setattr(self, f"sensor_{period}_fuel_consumed", ha_sensors.Sensor(
                self.ha_trip,
                name=f"Verbrauch {label}",
                state_topic_postfix=f"{period}/fuel_consumed",
                unit_of_measurement="L",
                device_class="volume",
                state_class="total_increasing",
                suggested_display_precision=2,
                icon="mdi:fuel",
            ))
            setattr(self, f"sensor_{period}_fuel_consumed_gauge", ha_sensors.Sensor(
                self.ha_trip,
                name=f"Verbrauch {label} (Tankanzeige)",
                state_topic_postfix=f"{period}/fuel_consumed_gauge",
                unit_of_measurement="L",
                device_class="volume",
                state_class="total",
                icon="mdi:gauge",
            ))
            setattr(self, f"sensor_{period}_fuel_consumed_difference", ha_sensors.Sensor(
                self.ha_trip,
                name=f"Verbrauch {label} Differenz (berechnet - Tankanzeige)",
                state_topic_postfix=f"{period}/fuel_consumed_difference",
                unit_of_measurement="L",
                state_class="measurement",
                suggested_display_precision=2,
                icon="mdi:delta",
            ))

        # lock (front/back/all_locked)

        self.sensor_lock_front = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Vordertüren Verriegelungsstatus",
            state_topic_postfix="lock/front",
            device_class="lock",
            payload_on="unlocked",
            payload_off="locked",
        )
        self.sensor_lock_back = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Hecktüren Verriegelungsstatus",
            state_topic_postfix="lock/back",
            device_class="lock",
            payload_on="unlocked",
            payload_off="locked",
        )
        self.sensor_lock_all_locked = ha_sensors.BinarySensor(
            self.ha_doors,
            name="Alle Türen verriegelt",
            state_topic_postfix="lock/all_locked",
            device_class="lock",
            payload_on="0",
            payload_off="1",
        )

        # lock button events

        self.event_lock_button_close = ha_sensors.Event(
            self.ha_doors,
            name="Fernbedienung Schließ-Knopf",
            state_topic_postfix="lock/button/close",
            event_types=["press", "long_press", "double_press", "release"],
            value_template="""{"event_type": "{{ value }}"}""",
        )
        self.event_lock_button_front = ha_sensors.Event(
            self.ha_doors,
            name="Fernbedienung Vordertüren-Knopf",
            state_topic_postfix="lock/button/front",
            event_types=["press", "long_press", "double_press", "release"],
            value_template="""{"event_type": "{{ value }}"}""",
        )
        self.event_lock_button_back = ha_sensors.Event(
            self.ha_doors,
            name="Fernbedienung Hecktüren-Knopf",
            state_topic_postfix="lock/button/back",
            event_types=["press", "long_press", "double_press", "release"],
            value_template="""{"event_type": "{{ value }}"}""",
        )

        # cruise control

        self.sensor_cruise_control_status = ha_sensors.Sensor(
            self.ha_driving,
            name="Tempomat Status",
            state_topic_postfix="cruise_control/status",
        )
        self.sensor_cruise_control_button_pressed = ha_sensors.BinarySensor(
            self.ha_driving,
            name="Tempomat Knopf gedrückt",
            state_topic_postfix="cruise_control/button_pressed",
            device_class="safety",
        )

        # reverse

        self.sensor_reverse = ha_sensors.BinarySensor(
            self.ha_driving,
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
        # computed, see ParkingDetector
        self.sensor_parking_likely = ha_sensors.BinarySensor(
            self.ha_driving,
            name="Parkvorgang wahrscheinlich",
            default_entity_id="binary_sensor.parkvorgang_wahrscheinlich",
            state_topic_postfix="parking_likely",
            icon="mdi:car-brake-parking",
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

        # fuel level in percent of the tank (byte 5), converted to litres
        # When the car is parked/shut down, the cluster sends zeroed frames.
        # A real tank level of 0 is not observable (the low-fuel warning kicks
        # in long before), so treat 0 as "no reading" and keep the last value.
        fuel_level_percent = msg.data[5]
        if fuel_level_percent:
            fuel_level = round(
                fuel_level_percent * self.FUEL_TANK_CAPACITY_L / 100, 1
            )
            self.publish(
                "fuel_level",
                fuel_level,
                only_if_changed=timedelta(seconds=30),
                retain=True,
            )
            self._fuel_level_l = fuel_level
            now = datetime.now()
            self._roll_over_day(now)
            self.period_today.update_fuel_gauge(fuel_level, now)
            self.period_trip.update_fuel_gauge(fuel_level, now)

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

        # accelerator pedal (byte 3): 0 = released, raw 0..255, mapped linearly
        # to percent. Verified with full-throttle captures: the pedal has two
        # end stops, 253 at the floor and 255 when pushed past the detent
        # (kickdown). Averaged and published at ~1 Hz.
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
        self._last_speed = (speed, datetime.now())
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

        now = datetime.now()
        self._roll_over_day(now)
        self._start_trip_if_needed(now)
        self._accumulate_odometry(msg, now)

        self.last_ignition_signal_acquired = now

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
        self._current_consumption = consumption
        self._current_consumption_time = datetime.now()

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
        self._last_reverse = (bool(reverse), datetime.now())
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
        self._last_speed = None  # (km/h, datetime)
        self._last_reverse = None  # (bool, datetime)
        parking_config = self.service_config.get("parking", {})
        self.parking = ParkingDetector(
            slow_kmh=parking_config.get("slow_kmh", 10),
            fast_kmh=parking_config.get("fast_kmh", 25),
            settle_time=timedelta(seconds=parking_config.get("settle_seconds", 60)),
        )
        self.create_ha_sensors()
        self._init_udp_sender()
        self._load_periods()

    # calculated distance and fuel per day and per trip

    def _day_key(self, now):
        # the day starts at DAY_START_HOUR, so a late-night drive counts
        # towards the day it started on
        return (now - timedelta(hours=self.DAY_START_HOUR)).date().isoformat()

    def _new_period(self, key):
        period = ConsumptionPeriod(key)
        # the gauge is only readable while the car is awake; start from the
        # last level seen
        if self._fuel_level_l is not None:
            period.update_fuel_gauge(self._fuel_level_l, datetime.now())
        return period

    def _load_periods(self):
        saved = self.state.get_path("periods", default={})
        today = saved.get("today")
        trip = saved.get("trip")
        now = datetime.now()

        # restore the last gauge level so new periods can start from it
        for data in (today, trip):
            if (
                data
                and data.get("gauge_version") == ConsumptionPeriod.GAUGE_VERSION
                and data.get("gauge_current_l") is not None
            ):
                self._fuel_level_l = data["gauge_current_l"]

        if today and today.get("key") == self._day_key(now):
            self.period_today = ConsumptionPeriod(today["key"], today)
        else:
            self.period_today = self._new_period(self._day_key(now))

        # the trip key is the timestamp of the last ignition signal
        if trip:
            self.period_trip = ConsumptionPeriod(trip.get("key"), trip)
        else:
            self.period_trip = self._new_period(None)

    def _save_periods(self):
        self.state.set_path(
            "periods",
            value={
                "today": self.period_today.to_dict(),
                "trip": self.period_trip.to_dict(),
            },
        )
        self.state.save()
        self._period_state_saved = datetime.now()

    def _roll_over_day(self, now):
        key = self._day_key(now)
        if self.period_today.key != key:
            self.log.info(f"New day {key}, resetting daily distance and fuel")
            self.period_today = self._new_period(key)
            self._save_periods()

    def _start_trip_if_needed(self, now):
        last_ignition = self.period_trip.key
        if (
            last_ignition is None
            or now.timestamp() - last_ignition
            >= self.TRIP_IGNITION_OFF_RESET.total_seconds()
        ):
            self.log.info("Ignition on after a long break, starting new trip")
            self.period_trip = self._new_period(now.timestamp())
            self._save_periods()
        else:
            self.period_trip.key = now.timestamp()

    def _accumulate_odometry(self, msg, now):
        # average of both wheel counters, independent of the manually started
        # distance accumulation above
        counters = (
            (msg.data[4] << 8 | msg.data[5]) & 0x1FFF,
            (msg.data[6] << 8 | msg.data[7]) & 0x1FFF,
        )
        last_counters = self._odometry_last_counters
        last_time = self._odometry_last_time
        self._odometry_last_counters = counters
        self._odometry_last_time = now
        if last_counters is None or now - last_time > self.WHEEL_COUNTER_MAX_GAP:
            return

        delta_counts = sum(
            (c - last_c) & 0x1FFF for c, last_c in zip(counters, last_counters)
        ) / len(counters)
        if not delta_counts:
            return
        distance_m = delta_counts / self.WHEEL_COUNTS_PER_M

        consumption = None
        if (
            self._current_consumption_time is not None
            and now - self._current_consumption_time <= self.CONSUMPTION_MAX_AGE
        ):
            consumption = self._current_consumption

        self.period_today.add_distance(distance_m, consumption)
        self.period_trip.add_distance(distance_m, consumption)

    @miqro.loop(seconds=5)
    def publish_periods(self):
        self._roll_over_day(datetime.now())
        for name, period in (("today", self.period_today), ("trip", self.period_trip)):
            self.publish_json_keys(
                period.values(),
                name,
                retain=True,
                qos=self.QOS_AT_LEAST_ONCE,
                only_if_changed=timedelta(minutes=5),
            )

        if (
            self._period_state_saved is None
            or datetime.now() - self._period_state_saved
            >= self.PERIOD_STATE_SAVE_INTERVAL
        ):
            self._save_periods()

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

    def publish_parking(self, ignition, now=None):
        now = now or datetime.now()
        speed = None
        if self._last_speed is not None and now - self._last_speed[1] <= self.SPEED_MAX_AGE:
            speed = self._last_speed[0]
        reverse = (
            self._last_reverse is not None
            and self._last_reverse[0]
            and now - self._last_reverse[1] <= self.REVERSE_MAX_AGE
        )
        parking = self.parking.update(now, ignition, speed, reverse)
        self.publish(
            "parking_likely",
            1 if parking else 0,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=timedelta(seconds=60),
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
        self.publish_parking(bool(ignition))

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
