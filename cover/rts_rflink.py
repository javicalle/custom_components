"""
Support for RTS Cover devices over RFLink controller.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/cover.rflink/
"""
import logging

import voluptuous as vol

from homeassistant.core import callback
from homeassistant.const import (
    STATE_OPEN, STATE_CLOSED, STATE_OPENING, STATE_CLOSING)

from homeassistant.components.rflink import (
    DATA_ENTITY_GROUP_LOOKUP, DATA_ENTITY_LOOKUP,
    DEVICE_DEFAULTS_SCHEMA, EVENT_KEY_COMMAND, RflinkCommand)
from homeassistant.components.cover import (
    CoverDevice, PLATFORM_SCHEMA, SUPPORT_OPEN, SUPPORT_CLOSE,
    SUPPORT_STOP, SUPPORT_SET_POSITION, ATTR_POSITION)
from homeassistant.helpers.event import async_track_utc_time_change
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME

REQUIREMENTS = ['xknx==0.8.5']

DEPENDENCIES = ['rflink']

_LOGGER = logging.getLogger(__name__)


CONF_ALIASES = 'aliases'
CONF_GROUP_ALIASES = 'group_aliases'
CONF_GROUP = 'group'
CONF_NOGROUP_ALIASES = 'nogroup_aliases'
CONF_DEVICE_DEFAULTS = 'device_defaults'
CONF_DEVICES = 'devices'
CONF_AUTOMATIC_ADD = 'automatic_add'
CONF_FIRE_EVENT = 'fire_event'
CONF_IGNORE_DEVICES = 'ignore_devices'
CONF_RECONNECT_INTERVAL = 'reconnect_interval'
CONF_SIGNAL_REPETITIONS = 'signal_repetitions'
CONF_WAIT_FOR_ACK = 'wait_for_ack'

CONF_MY_POSITION = 'rts_my_position'
CONF_TRAVELLING_TIME_DOWN = 'travelling_time_down'
CONF_TRAVELLING_TIME_UP = 'travelling_time_up'

DEFAULT_TRAVEL_TIME = 25

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_DEVICE_DEFAULTS, default=DEVICE_DEFAULTS_SCHEMA({})):
    DEVICE_DEFAULTS_SCHEMA,
    vol.Optional(CONF_DEVICES, default={}): vol.Schema({
        cv.string: {
            vol.Optional(CONF_NAME): cv.string,
            vol.Optional(CONF_ALIASES, default=[]):
                vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(CONF_GROUP_ALIASES, default=[]):
                vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(CONF_NOGROUP_ALIASES, default=[]):
                vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(CONF_FIRE_EVENT, default=False): cv.boolean,
            vol.Optional(CONF_SIGNAL_REPETITIONS): vol.Coerce(int),
            vol.Optional(CONF_GROUP, default=True): cv.boolean,
            vol.Optional(CONF_MY_POSITION):cv.positive_int,
            vol.Optional(CONF_TRAVELLING_TIME_DOWN, default=DEFAULT_TRAVEL_TIME):
                cv.positive_int,
            vol.Optional(CONF_TRAVELLING_TIME_UP, default=DEFAULT_TRAVEL_TIME):
                cv.positive_int,
        },
    }),
})


def devices_from_config(domain_config, hass=None):
    """Parse configuration and add RFLink cover devices."""
    devices = []
    for device_id, config in domain_config[CONF_DEVICES].items():
        rts_my_position = config.get(CONF_MY_POSITION)
        del config[CONF_MY_POSITION]
        travel_time_down = config.get(CONF_TRAVELLING_TIME_DOWN)
        del config[CONF_TRAVELLING_TIME_DOWN]
        travel_time_up = config.get(CONF_TRAVELLING_TIME_UP)
        del config[CONF_TRAVELLING_TIME_UP]
        device_config = dict(domain_config[CONF_DEVICE_DEFAULTS], **config)
        device = RTSRflinkCover(device_id, hass, rts_my_position,
                travel_time_down, travel_time_up, **device_config)
        devices.append(device)

        # Register entity (and aliases) to listen to incoming RFLink events
        # Device id and normal aliases respond to normal and group command
        hass.data[DATA_ENTITY_LOOKUP][
            EVENT_KEY_COMMAND][device_id].append(device)
        _LOGGER.debug("cover device %s registered", device_id)
        if config[CONF_GROUP]:
            hass.data[DATA_ENTITY_GROUP_LOOKUP][
                EVENT_KEY_COMMAND][device_id].append(device)
            _LOGGER.debug("cover group %s registered", device_id)
        for _id in config[CONF_ALIASES]:
            hass.data[DATA_ENTITY_LOOKUP][
                EVENT_KEY_COMMAND][_id].append(device)
            _LOGGER.debug("cover alias %s registered", _id)
            hass.data[DATA_ENTITY_GROUP_LOOKUP][
                EVENT_KEY_COMMAND][_id].append(device)
            _LOGGER.debug("cover group %s registered", _id)
    return devices


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up the RFLink cover platform."""
    async_add_entities(devices_from_config(config, hass))


class RTSRflinkCover(RflinkCommand, CoverDevice):
    """RFLink entity which can switch on/stop/off (eg: cover)."""

    def __init__(self, hass, device_id, rts_my_position,
                 travel_time_down, travel_time_up, **device_config):
        """Initialize the cover."""
        from xknx.devices import TravelCalculator
        self.hass = hass
        self._rts_my_position = rts_my_position
        self._travel_time_down = travel_time_down
        self._travel_time_up = travel_time_up
        self._require_stop_cover = False

#        self.async_register_callbacks()

        self._unsubscribe_auto_updater = None

        super().__init__(device_id, hass, **device_config)

        self.travelcalculator = TravelCalculator(
            self._travel_time_down,
            self._travel_time_up)

    def _handle_event(self, event):
        """Adjust state if RFLink picks up a remote command for this device."""
        self.cancel_queued_send_commands()

        command = event['command']
        if command in ['on', 'allon', 'up']:
            self._require_stop_cover = False
            self.travelcalculator.start_travel_up()
            self.start_auto_updater()
        elif command in ['off', 'alloff', 'down']:
            self._require_stop_cover = False
            self.travelcalculator.start_travel_down()
            self.start_auto_updater()
        elif command in ['stop']:
            _LOGGER.debug('_handle_even :: stop')
            self._require_stop_cover = False
            self._handle_my_button()

    def _handle_my_button(self):
        """Handle the MY button press"""
        if self.travelcalculator.is_traveling():
            _LOGGER.debug('_handle_my_button :: button stops cover')
            self.travelcalculator.stop()
            self.stop_auto_updater()
        elif self._rts_my_position is not None:
            _LOGGER.debug('_handle_my_button :: button sends to MY')
            self.travelcalculator.start_travel(self._rts_my_position)
            self.start_auto_updater()

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
        if self.travelcalculator is not None:
            attr['travel_to_position'] = self.travelcalculator.travel_to_position 
            attr['tc_current_position'] = self.travelcalculator.current_position()
        #     attr['position_type'] = self.travelcalculator.position_type
        return attr

    @property
    def should_poll(self):
        """No polling available in RFLink cover."""
        return False

    @property
    def supported_features(self):
        """Flag supported features."""
        supported_features = SUPPORT_OPEN | SUPPORT_CLOSE | \
            SUPPORT_SET_POSITION | SUPPORT_STOP
        return supported_features

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self.travelcalculator.current_position()

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        from xknx.devices import TravelStatus
        return self.travelcalculator.is_traveling() and \
            self.travelcalculator.travel_direction == TravelStatus.DIRECTION_UP

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        from xknx.devices import TravelStatus
        return self.travelcalculator.is_traveling() and \
            self.travelcalculator.travel_direction == TravelStatus.DIRECTION_DOWN

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self.travelcalculator.is_closed()

    @property
    def assumed_state(self):
        """Return True because covers can be stopped midway."""
        return STATE_OPEN

    def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = kwargs[ATTR_POSITION]
            _LOGGER.debug('async_set_cover_position: %d', position)
            self._require_stop_cover = True
            self.start_auto_updater()
            return self.set_position(position)

    def async_close_cover(self, **kwargs):
        """Turn the device close."""
        _LOGGER.debug('async_close_cover')
        self.travelcalculator.start_travel_down()
        self._require_stop_cover = False
        self.start_auto_updater()
        return self._async_handle_command('close_cover')

    def async_open_cover(self, **kwargs):
        """Turn the device open."""
        _LOGGER.debug('async_open_cover')
        self.travelcalculator.start_travel_up()
        self._require_stop_cover = False
        self.start_auto_updater()
        return self._async_handle_command('open_cover')

    def async_stop_cover(self, **kwargs):
        """Turn the device stop."""
        _LOGGER.debug('async_stop_cover')
        self._require_stop_cover = False
        self._handle_my_button()
        return self._async_handle_command('stop_cover')

    async def set_position(self, position):
        _LOGGER.debug('set_position')
        """Move cover to a designated position."""
        current_position = self.travelcalculator.current_position()
        _LOGGER.debug('set_position :: current_position: %d, new_position: %d', current_position, position)
        command = None
        if position < current_position:
            command = 'close_cover'
        elif position > current_position:
            command = 'open_cover'
        self.travelcalculator.start_travel(position)
        _LOGGER.debug('set_position :: command %s', command)
        await self._async_handle_command(command)
        return

    def start_auto_updater(self):
        """Start the autoupdater to update HASS while cover is moving."""
        _LOGGER.debug('start_auto_updater')
        if self._unsubscribe_auto_updater is None:
            _LOGGER.debug('init _unsubscribe_auto_updater')
            self._unsubscribe_auto_updater = async_track_utc_time_change(
                self.hass, self.auto_updater_hook)

    @callback
    def auto_updater_hook(self, now):
        """Call for the autoupdater."""
        self.async_schedule_update_ha_state()
        if self.position_reached():
            _LOGGER.debug('auto_updater_hook :: position_reached')
            self.stop_auto_updater()

        self.hass.add_job(self.auto_stop_if_necessary())

    def stop_auto_updater(self):
        """Stop the autoupdater."""
        _LOGGER.debug('stop_auto_updater')
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    def position_reached(self):
        """Return if cover has reached its final position."""
        return self.travelcalculator.position_reached()

    async def auto_stop_if_necessary(self):
        """Do auto stop if necessary."""
        # If device does not support auto_positioning,
        # we have to stop the device when position is reached.
        # unless device was traveling to fully open
        # or fully closed state
        if self.position_reached():
            if self._require_stop_cover:
                _LOGGER.debug('auto_stop_if_necessary :: calling stop command')
                await self._async_handle_command('stop_cover')
            self.travelcalculator.stop()

        # if (
        #         self._require_stop_cover and
        #         self.position_reached() and
        #         not self.travelcalculator.is_open() and
        #         not self.travelcalculator.is_closed()):
        #     _LOGGER.debug('auto_stop_if_necessary :: calling stop command')
        #     self.travelcalculator.stop()
        #     await self._async_handle_command('stop_cover')
