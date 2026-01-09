import miqro
from miqro import ha_sensors
from datetime import datetime, timedelta
from can.interfaces.socketcan import SocketcanBus
from can import CanError
from collections import deque
import os
from time import sleep
from fuel_tracker import FuelTracker, FuelEntry, FuelType
from fuel_stats import FuelStatistics


class CANService(miqro.Service):
    SERVICE_NAME = "can"
    CAN_CHANNEL = "can0"
    CAN_TIMEOUT = 0.005

    LOOP_INTERVAL = 0.02

    SPEED_CAL_ADD = 0
    SPEED_CAL_MULT = 1.01
    
    # Fuel tracking configuration
    FUEL_CSV_PATH = "/var/lib/miqro/fuel_log.csv"
    FUEL_TYPES_CONFIG = ["Diesel", "HVO100", "Premium Diesel"]

    angle_pitch = deque(maxlen=10)
    angle_roll = deque(maxlen=10)
    speed = deque(maxlen=10)
    last_attitude_acquired = None
    last_ignition_signal_acquired = None
    last_lock_status = {"front": True, "back": True}  # True is locked
    debug = False
    
    # Fuel tracking state
    current_odometer = 0
    fuel_tracker = None
    fuel_stats = None

    CAN_ATTITUDE_MAX_DELAY = timedelta(seconds=0.2)
    CAN_ACTIVITY_TIMEOUT = timedelta(seconds=3)
    IGNITION_TIMEOUT = timedelta(seconds=3)

    def create_ha_sensors(self):
        self.device_car = ha_sensors.Device(
            self,
            name="Basisfahrzeug",
            model="Jumper",
            manufacturer="Citroën",
            identifiers=["SU RF 2616"],
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

        # fuel

        self.sensor_current_consumption = ha_sensors.Sensor(
            self.device_car,
            name="Aktueller Verbrauch",
            state_topic_postfix="current_consumption",
            unit_of_measurement="l/100km",
            state_class="measurement",
        )
        self.sensor_overall_kilometers = ha_sensors.Sensor(
            self.device_car,
            name="Gesamt-Kilometerstand",
            state_topic_postfix="overall_kilometers",
            unit_of_measurement="km",
            device_class="distance",
            state_class="total_increasing",
        )
        self.sensor_overall_kilometers_last_stop = ha_sensors.Sensor(
            self.device_car,
            name="Gesamtkilometer letzter Halt",
            state_topic_postfix="overall_kilometers_last_stop",
            unit_of_measurement="km",
            device_class="distance",
            state_class="measurement",
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

        # TODO: buttons on remote

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

        # Number inputs for liters and cost (Home Assistant MQTT Number)
        self.input_fuel_liters = ha_sensors.Number(
            self.device_car,
            name="Tanken: Liter",
            state_topic_postfix="fuel/input/liters/state",
            command_topic_postfix="fuel/input/liters",
            unit_of_measurement="L",
        )
        self.input_fuel_cost = ha_sensors.Number(
            self.device_car,
            name="Tanken: Kosten",
            state_topic_postfix="fuel/input/cost/state",
            command_topic_postfix="fuel/input/cost",
            unit_of_measurement="€",
        )

        # Select for fuel type
        self.input_fuel_type = ha_sensors.Select(
            self.device_car,
            name="Tanken: Kraftstofftyp",
            state_topic_postfix="fuel/input/fuel_type/state",
            command_topic_postfix="fuel/input/fuel_type",
            options=self.FUEL_TYPES_CONFIG,
        )

        # Binary select for partial/full (use a select to allow HA compatibility)
        self.input_fuel_partial = ha_sensors.Select(
            self.device_car,
            name="Tanken: Teilfüllung",
            state_topic_postfix="fuel/input/partial/state",
            command_topic_postfix="fuel/input/partial",
            options=["no", "yes"],
        )

        # Button to confirm the entry
        self.input_fuel_confirm = ha_sensors.Button(
            self.device_car,
            name="Tanken: Bestätigen",
            command_topic_postfix="fuel/input/confirm",
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

    def handle_attitude(self, msg):
        if not msg.data[0] and not msg.data[1]:
            return
        self.angle_pitch.append(((msg.data[0] << 4 | msg.data[1] >> 4) - 2**11) / 10)
        self.angle_roll.append((((msg.data[1] & 0xF) << 8 | msg.data[2]) - 2**11) / 10)
        self.last_attitude_acquired = datetime.now()

    def handle_temperature(self, msg):
        temp = (msg.data[0] - 80) / 2
        self.publish("temperature/outside", temp, only_if_changed=True)

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
        if msg.data[1] == 0xFF and msg.data[2] == 0xFF:
            return
        oil = msg.data[1] << 8 | msg.data[2]
        self.publish(
            "maintenance/oil_change_km",
            oil,
            qos=self.QOS_AT_LEAST_ONCE,
            only_if_changed=True,
            retain=True,
        )

        unknown = (msg.data[4] << 4 | msg.data[5] >> 4) * 16
        self.publish(
            "maintenance/unknown_km",
            unknown,
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
            round(int(second_nibble << 8 | msg.data[3]) / 16) * self.SPEED_CAL_MULT
            + self.SPEED_CAL_ADD
        )
        self.speed.append(speed)
        self.publish("speed", speed, qos=self.QOS_AT_LEAST_ONCE)

        self.last_ignition_signal_acquired = datetime.now()

    def handle_fuel(self, msg):
        # first three nibbles represent current fuel consumption, as ints
        some_number = float("%x.%x" % (msg.data[0], msg.data[1] >> 4))
        self.publish("current_consumption", some_number, qos=self.QOS_AT_LEAST_ONCE)

        # next five nibbles are overall kilometers
        overall_kilometers = int(
            (msg.data[1] & 0xF) << 16 | msg.data[2] << 8 | msg.data[3]
        )
        # remember last seen overall kilometers so we can publish it on ignition-off
        self.last_overall_kilometers = overall_kilometers
        
        # Track current odometer for fuel statistics
        self.current_odometer = overall_kilometers
        
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
        self.bus = can_cls(self.CAN_CHANNEL, can_filters=self.CAN_IDS.values())
        self.last_canbus_message_received = None
        self.create_ha_sensors()
        # last seen overall kilometers from CAN messages
        self.last_overall_kilometers = None
        # whether we've published the last-stop value for the current ignition-off
        self._last_stop_published = False
        # track last ignition state to detect transitions (0 or 1)
        self._last_ignition_state = None
        
        # Initialize fuel tracking
        # Check for custom config path in environment or config
        fuel_csv_path = os.getenv(
            "MIQRO_FUEL_CSV_PATH",
            self.config.get("fuel_csv_path", self.FUEL_CSV_PATH)
        )
        self.fuel_tracker = FuelTracker(fuel_csv_path)
        self.fuel_stats = FuelStatistics(self.fuel_tracker)
        # Pending input values (for HA Number/Select -> confirm flow)
        self._pending_fuel_liters = None
        self._pending_fuel_cost = None
        self._pending_fuel_type = None
        self._pending_fuel_partial = False

        # Publish HA discovery (via ha_sensors) and initial stats on startup
        try:
            # allow a short delay for MQTT connection to establish
            self.publish_fuel_stats()
        except Exception:
            pass

    def try_recover(self):
        # ifdown can0 and ifup can0 when the CAN bus is not working
        os.system(f"ip link set {self.CAN_CHANNEL} down")
        sleep(1)
        os.system(f"ip link set {self.CAN_CHANNEL} up type can bitrate 50000")

    @miqro.loop(seconds=0)
    def recv_from_can(self):
        try:
            msg = self.bus.recv(timeout=self.CAN_TIMEOUT)
        except CanError:
            self.try_recover()
            return

        if msg is None:
            return
        self.last_canbus_message_received = datetime.now()

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
        # detect ignition transitions to publish overall_kilometers_last_stop once
        current_ignition = ignition
        prev_ignition = getattr(self, "_last_ignition_state", None)
        if prev_ignition is None:
            self._last_ignition_state = current_ignition
        else:
            if prev_ignition == 1 and current_ignition == 0:
                # ignition turned off - publish the last seen overall kilometers once
                if not getattr(self, "_last_stop_published", False):
                    if self.last_overall_kilometers is not None:
                        self.publish(
                            "overall_kilometers_last_stop",
                            self.last_overall_kilometers,
                            qos=self.QOS_AT_LEAST_ONCE,
                            only_if_changed=False,
                            retain=True,
                        )
                    self._last_stop_published = True
            elif prev_ignition == 0 and current_ignition == 1:
                # ignition turned on - reset published flag so next off will publish
                self._last_stop_published = False
            self._last_ignition_state = current_ignition

        self.publish("ignition", ignition, only_if_changed=timedelta(seconds=60))

    @miqro.handle("debug")
    def handle_debug(self, message):
        self.debug = message in ["1", "true", "on"]
    
    @miqro.handle("fuel/refuel")
    def handle_fuel_refuel(self, message):
        """Handle refueling event via MQTT.
        
        Expected message format (JSON):
        {
            "liters": float,
            "cost": float,
            "fuel_type": "Diesel" | "HVO100" | "Premium Diesel",
            "partial": bool (optional, default false)
        }
        """
        try:
            import json
            data = json.loads(message)
            
            liters = float(data.get("liters"))
            cost = float(data.get("cost"))
            fuel_type_str = data.get("fuel_type", "Diesel")
            partial = data.get("partial", False)
            
            if liters <= 0:
                self.log.error(f"Invalid liters amount: {liters}")
                self.publish("fuel/refuel/status", "error_invalid_liters")
                return
            
            if cost < 0:
                self.log.error(f"Invalid cost amount: {cost}")
                self.publish("fuel/refuel/status", "error_invalid_cost")
                return
            
            # Parse fuel type
            fuel_type = FuelType.from_string(fuel_type_str)
            
            # Create fuel entry with current odometer
            entry = FuelEntry(
                odometer=self.current_odometer,
                liters=liters,
                cost=cost,
                fuel_type=fuel_type,
                partial=partial,
            )
            
            # Store in database
            self.fuel_tracker.add_entry(entry)
            self.log.info(
                f"Fuel refuel recorded: {liters}L of {fuel_type.value} "
                f"at {self.current_odometer}km (cost: {cost})"
            )
            self.publish("fuel/refuel/status", "success")

            # Publish stats immediately after a refuel
            try:
                self.publish_fuel_stats()
            except Exception:
                pass
            
        except json.JSONDecodeError:
            self.log.error(f"Invalid JSON in fuel refuel message: {message}")
            self.publish("fuel/refuel/status", "error_invalid_json")
        except ValueError as e:
            self.log.error(f"Invalid fuel data: {e}")
            self.publish("fuel/refuel/status", "error_invalid_data")
        except Exception as e:
            self.log.error(f"Error processing fuel refuel: {e}")
            self.publish("fuel/refuel/status", "error_unknown")
    
    def publish_fuel_stats(self):
        """Calculate fuel statistics and publish them to MQTT (HA-friendly topics).

        This is called on startup and immediately after a refuel.
        """
        try:
            stats = self.fuel_stats.get_statistics_summary(self.current_odometer)

            # Publish basic scalar statistics
            avg_l = stats.get("average_consumption_l_per_100km")
            avg_km_per_l = stats.get("average_consumption_km_per_liter")
            last_leg = stats.get("last_leg_consumption")

            if avg_l is None:
                self.publish("fuel/stats/average_l_per_100km", "null")
            else:
                self.publish("fuel/stats/average_l_per_100km", round(avg_l, 3))

            if avg_km_per_l is None:
                self.publish("fuel/stats/average_km_per_l", "null")
            else:
                self.publish("fuel/stats/average_km_per_l", round(avg_km_per_l, 3))

            if last_leg is None:
                self.publish("fuel/stats/last_leg_consumption", "null")
            else:
                self.publish("fuel/stats/last_leg_consumption", round(last_leg, 3))

            # Publish JSON blobs for per-type values
            self.publish_json("fuel/stats/consumption_by_type", stats.get("consumption_by_fuel_type", {}))
            self.publish_json("fuel/stats/cost_per_liter_by_type", stats.get("cost_per_liter_by_type", {}))
            self.publish_json("fuel/stats/cost_per_km_by_type", stats.get("cost_per_km_by_type", {}))

            # Totals
            self.publish("fuel/stats/total_entries", stats.get("total_entries", 0))
            self.publish("fuel/stats/full_refuels", stats.get("full_refuels", 0))

            self.log.info("Fuel statistics published")
        except Exception as e:
            self.log.error(f"Error calculating fuel statistics: {e}")
            self.publish("fuel/stats/error", str(e))

    @miqro.handle("fuel/input/liters")
    def handle_input_liters(self, message):
        try:
            val = float(message)
            self._pending_fuel_liters = val
            self.publish("fuel/input/liters/state", val)
        except Exception:
            self.log.error(f"Invalid liters input: {message}")
            self.publish("fuel/input/liters/status", "error")

    @miqro.handle("fuel/input/cost")
    def handle_input_cost(self, message):
        try:
            val = float(message)
            self._pending_fuel_cost = val
            self.publish("fuel/input/cost/state", val)
        except Exception:
            self.log.error(f"Invalid cost input: {message}")
            self.publish("fuel/input/cost/status", "error")

    @miqro.handle("fuel/input/fuel_type")
    def handle_input_fuel_type(self, message):
        try:
            # accept plain fuel type string
            self._pending_fuel_type = message
            self.publish("fuel/input/fuel_type/state", message)
        except Exception:
            self.log.error(f"Invalid fuel type input: {message}")
            self.publish("fuel/input/fuel_type/status", "error")

    @miqro.handle("fuel/input/partial")
    def handle_input_partial(self, message):
        try:
            val = str(message).lower() in ["1", "true", "yes", "on"]
            self._pending_fuel_partial = val
            self.publish("fuel/input/partial/state", str(val))
        except Exception:
            self.log.error(f"Invalid partial input: {message}")
            self.publish("fuel/input/partial/status", "error")

    @miqro.handle("fuel/input/confirm")
    def handle_input_confirm(self, message):
        """Confirm and record the pending fuel input values.

        The confirm topic can be triggered by a HA Button (payloads like "PRESS" or "1").
        """
        payload = str(message).lower()
        if payload not in ["1", "press", "press", "p", "on", "confirm"] and payload != "":
            # Some buttons send an empty payload on press; accept that too
            pass

        # Validate pending values
        if self._pending_fuel_liters is None or self._pending_fuel_cost is None:
            self.log.error("Pending fuel input incomplete; cannot confirm")
            self.publish("fuel/refuel/status", "error_incomplete_input")
            return

        fuel_type_str = self._pending_fuel_type or "Diesel"
        try:
            fuel_type = FuelType.from_string(fuel_type_str)
        except Exception:
            self.log.error(f"Unknown fuel type on confirm: {fuel_type_str}")
            self.publish("fuel/refuel/status", "error_unknown_fuel_type")
            return

        entry = FuelEntry(
            odometer=self.current_odometer,
            liters=self._pending_fuel_liters,
            cost=self._pending_fuel_cost,
            fuel_type=fuel_type,
            partial=bool(self._pending_fuel_partial),
        )

        self.fuel_tracker.add_entry(entry)
        self.log.info(f"Fuel refuel recorded via inputs: {entry.liters}L {entry.fuel_type.value} at {entry.odometer}km")
        self.publish("fuel/refuel/status", "success")

        # reset pending inputs
        self._pending_fuel_liters = None
        self._pending_fuel_cost = None
        self._pending_fuel_type = None
        self._pending_fuel_partial = False

        # Publish fresh stats after refuel
        try:
            self.publish_fuel_stats()
        except Exception:
            pass


if __name__ == "__main__":
    miqro.run(CANService)
