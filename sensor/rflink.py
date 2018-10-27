"""
Support for Rflink sensors.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/light.rflink/
"""
from functools import partial
import logging

from homeassistant.components.rflink import (
    CONF_ALIASES, CONF_ALIASSES, CONF_AUTOMATIC_ADD, CONF_DEVICES,
    DATA_DEVICE_REGISTER, DATA_ENTITY_LOOKUP, EVENT_KEY_ID,
    EVENT_KEY_SENSOR, EVENT_KEY_UNIT, RflinkDevice, cv, remove_deprecated, vol,
    SIGNAL_AVAILABILITY, SIGNAL_HANDLE_EVENT, TMP_ENTITY)
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT, CONF_NAME, CONF_UNIT_OF_MEASUREMENT)
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send, async_dispatcher_connect)

DEPENDENCIES = ['rflink']

_LOGGER = logging.getLogger(__name__)

SENSOR_ICONS = {
    'humidity': 'mdi:water-percent',
    'battery': 'mdi:battery',
    'temperature': 'mdi:thermometer',
}

CONF_SENSOR_TYPE = 'sensor_type'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_AUTOMATIC_ADD, default=True): cv.boolean,
    vol.Optional(CONF_DEVICES, default={}): {
        cv.string: vol.Schema({
            vol.Optional(CONF_NAME): cv.string,
            vol.Required(CONF_SENSOR_TYPE): cv.string,
            vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
            vol.Optional(CONF_ALIASES, default=[]):
                vol.All(cv.ensure_list, [cv.string]),
            # deprecated config options
            vol.Optional(CONF_ALIASSES):
                vol.All(cv.ensure_list, [cv.string]),
        })
    },
}, extra=vol.ALLOW_EXTRA)


def lookup_unit_for_sensor_type(sensor_type):
    """Get unit for sensor type.

    Async friendly.
    """
    from rflink.parser import UNITS, PACKET_FIELDS
    field_abbrev = {v: k for k, v in PACKET_FIELDS.items()}

    return UNITS.get(field_abbrev.get(sensor_type))


def devices_from_config(domain_config):
    """Parse configuration and add Rflink sensor devices."""
    devices = []
    for device_id, config in domain_config[CONF_DEVICES].items():
        if ATTR_UNIT_OF_MEASUREMENT not in config:
            config[ATTR_UNIT_OF_MEASUREMENT] = lookup_unit_for_sensor_type(
                config[CONF_SENSOR_TYPE])
        remove_deprecated(config)
        device = RflinkSensor(None, device_id, **config)
        devices.append(device)

    return devices


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up the Rflink platform."""
    async_add_entities(devices_from_config(config))

    async def add_new_device(event):
        """Check if device is known, otherwise create device entity."""
        device_id = event[EVENT_KEY_ID]

        rflinksensor = partial(RflinkSensor, event, device_id)
        device = rflinksensor(event[EVENT_KEY_SENSOR], event[EVENT_KEY_UNIT])
        # Add device entity
        async_add_entities([device])

    if config[CONF_AUTOMATIC_ADD]:
        hass.data[DATA_DEVICE_REGISTER][EVENT_KEY_SENSOR] = add_new_device


class RflinkSensor(RflinkDevice):
    """Representation of a Rflink sensor."""

    def __init__(self, initial_event, device_id, sensor_type,
                 unit_of_measurement, **kwargs):
        """Handle sensor specific args and super init."""
        self._sensor_type = sensor_type
        self._unit_of_measurement = unit_of_measurement
        super().__init__(initial_event, device_id, **kwargs)

    def _handle_event(self, event):
        """Domain specific event handler."""
        self._state = event['value']

    async def async_added_to_hass(self):
        """Register update callback."""
        # Remove temporary bogus entity_id if added
        tmp_entity = TMP_ENTITY.format(self._device_id)
        if tmp_entity in self.hass.data[DATA_ENTITY_LOOKUP][
                EVENT_KEY_SENSOR][self._device_id]:
            self.hass.data[DATA_ENTITY_LOOKUP][
                EVENT_KEY_SENSOR][self._device_id].remove(tmp_entity)

        # Register id and aliases
        self.hass.data[DATA_ENTITY_LOOKUP][
            EVENT_KEY_SENSOR][self._device_id].append(self.entity_id)
        if self._aliases:
            for _id in self._aliases:
                self.hass.data[DATA_ENTITY_LOOKUP][
                    EVENT_KEY_SENSOR][_id].append(self.entity_id)
        async_dispatcher_connect(self.hass, SIGNAL_AVAILABILITY,
                                 self._availability_callback)
        async_dispatcher_connect(self.hass,
                                 SIGNAL_HANDLE_EVENT.format(self.entity_id),
                                 self.handle_event_callback)

        # Send signal to process the initial event after entity is created
        if self._initial_event:
            async_dispatcher_send(self.hass,
                                  SIGNAL_HANDLE_EVENT.format(self.entity_id),
                                  self._initial_event)

    @property
    def device_state_attributes(self):
        """Return the device state attributes."""
        attr = {}
        super_attr = super().device_state_attributes
        if super_attr is not None:
            attr.update(super_attr)

        if self._rts_my_position is not None:
            attr[CONF_MY_POSITION] = self._rts_my_position
        if self._travel_time_down is not None:
            attr[CONF_TRAVELLING_TIME_DOWN] = self._travel_time_down
        if self._travel_time_up is not None:
            attr[CONF_TRAVELLING_TIME_UP] = self._travel_time_up
        if self.tc is not None:
            attr['travel_to_position'] = self.tc.travel_to_position
            attr['tc_current_position'] = self.tc.current_position()
        #     attr['position_type'] = self.travelcalculator.position_type
        return attr

    @property
    def unit_of_measurement(self):
        """Return measurement unit."""
        return self._unit_of_measurement

    @property
    def state(self):
        """Return value."""
        return self._state

    @property
    def icon(self):
        """Return possible sensor specific icon."""
        if self._sensor_type in SENSOR_ICONS:
            return SENSOR_ICONS[self._sensor_type]
