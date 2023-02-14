"""Support for RTS Cover devices over RFLink controller."""
import logging

import voluptuous as vol

from datetime import timedelta

from homeassistant.core import callback

from homeassistant.helpers.event import async_track_utc_time_change, async_track_time_interval
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    PLATFORM_SCHEMA as COVER_PLATFORM_SCHEMA,
    CoverEntity,
)
from homeassistant.const import (
    CONF_DEVICES,
    CONF_NAME,
    CONF_TYPE,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
)

import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity

from homeassistant.components.rflink import (
    CONF_ALIASES,
    CONF_DEVICE_DEFAULTS,
    CONF_FIRE_EVENT,
    CONF_GROUP,
    CONF_GROUP_ALIASES,
    CONF_NOGROUP_ALIASES,
    CONF_SIGNAL_REPETITIONS,
    DEVICE_DEFAULTS_SCHEMA,
    EVENT_KEY_COMMAND,
    RflinkCommand,
)

from .travelcalculator import TravelCalculator

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

TYPE_STANDARD = "standard"
TYPE_INVERTED = "inverted"

CONF_MY_POSITION = 'rts_my_position'
CONF_TRAVELLING_TIME_DOWN = 'travelling_time_down'
CONF_TRAVELLING_TIME_UP = 'travelling_time_up'
DEFAULT_TRAVEL_TIME = 25

PLATFORM_SCHEMA = COVER_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(
            CONF_DEVICE_DEFAULTS, default=DEVICE_DEFAULTS_SCHEMA({})
        ): DEVICE_DEFAULTS_SCHEMA,
        vol.Optional(CONF_DEVICES, default={}): vol.Schema(
            {
                cv.string: {
                    vol.Optional(CONF_NAME): cv.string,
                    vol.Optional(CONF_TYPE, default=TYPE_STANDARD): vol.Any(TYPE_STANDARD, TYPE_INVERTED),
                    vol.Optional(CONF_ALIASES, default=[]):
                        vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional(CONF_GROUP_ALIASES, default=[]): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(CONF_NOGROUP_ALIASES, default=[]):
                        vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional(CONF_FIRE_EVENT, default=False): cv.boolean,
                    vol.Optional(CONF_SIGNAL_REPETITIONS): vol.Coerce(int),
                    vol.Optional(CONF_GROUP, default=True): cv.boolean,
                    vol.Optional(CONF_MY_POSITION):
                        vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                    vol.Optional(CONF_TRAVELLING_TIME_DOWN, default=DEFAULT_TRAVEL_TIME):
                        cv.positive_int,
                    vol.Optional(CONF_TRAVELLING_TIME_UP, default=DEFAULT_TRAVEL_TIME):
                        cv.positive_int,
                }
            }
        ),
    }
)


def entity_class_for_type(entity_type):
    """Translate entity type to entity class.
    Async friendly.
    """
    entity_device_mapping = {
        # default cover implementation
        TYPE_STANDARD: RTSRflinkCover,
        # cover with open/close commands inverted
        # like KAKU/COCO ASUN-650
        TYPE_INVERTED: InvertedRTSRflinkCover,
    }

    return entity_device_mapping.get(entity_type, RTSRflinkCover)


def devices_from_config(domain_config):
    """Parse configuration and add RFLink cover devices."""
    devices = []
    for entity_id, config in domain_config[CONF_DEVICES].items():
        entity_type = config.pop(CONF_TYPE)
        entity_class = entity_class_for_type(entity_type)
        rts_my_position = config.pop(CONF_MY_POSITION)
        travel_time_down = config.pop(CONF_TRAVELLING_TIME_DOWN)
        travel_time_up = config.pop(CONF_TRAVELLING_TIME_UP)
        device_config = dict(domain_config[CONF_DEVICE_DEFAULTS], **config)
        device = entity_class(
            entity_id, rts_my_position, travel_time_down,
            travel_time_up, **device_config)
        devices.append(device)
    return devices


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up the RFLink cover platform."""
    async_add_entities(devices_from_config(config))


class RTSRflinkCover(RflinkCommand, CoverEntity, RestoreEntity):
    """RFLink entity which can switch on/stop/off (eg: cover)."""

    def __init__(self, entity_id, rts_my_position,
                 travel_time_down, travel_time_up, **device_config):
        """Initialize the cover."""
        self._rts_my_position = rts_my_position
        self._travel_time_down = travel_time_down
        self._travel_time_up = travel_time_up
        self._require_stop_cover = False

#        self.async_register_callbacks()

        self._unsubscribe_auto_updater = None

        super().__init__(entity_id, None, **device_config)

        self.tc = TravelCalculator(
            self._travel_time_down, self._travel_time_up)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        """ Only cover's position matters.             """
        """ The rest is calculated from this attribute."""
        old_state = await self.async_get_last_state()
        _LOGGER.debug('async_added_to_hass :: oldState %s', old_state)
        if (
                old_state is not None and
                self.tc is not None and
                old_state.attributes.get(ATTR_CURRENT_POSITION) is not None):
            self.tc.set_position(self.shift_position(int(
                old_state.attributes.get(ATTR_CURRENT_POSITION))))

    def _handle_event(self, event):
        """Adjust state if RFLink picks up a remote command for this device."""
        self.cancel_queued_send_commands()
        _LOGGER.debug('_handle_event %s', event)

        # this must be wrong. ON command closes cover
        # command = event['command']
        command = event.get(EVENT_KEY_COMMAND)
        if command in ['on', 'allon', 'up']:
            self._require_stop_cover = False
            self.tc.start_travel_up()
            self.start_auto_updater()
        elif command in ['off', 'alloff', 'down']:
            self._require_stop_cover = False
            self.tc.start_travel_down()
            self.start_auto_updater()
        elif command in ['stop']:
            self._handle_my_button()

    def _handle_my_button(self):
        """Handle the MY button press"""
        self._require_stop_cover = False
        if self.tc.is_traveling():
            _LOGGER.debug('_handle_my_button :: button stops cover')
            self.tc.stop()
            self.stop_auto_updater()
        elif self._rts_my_position is not None:
            _LOGGER.debug('_handle_my_button :: button sends to MY')
            self.tc.start_travel(self.shift_position(self._rts_my_position))
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
        return attr

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self.shift_position(self.tc.current_position())

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        return self.tc.is_opening()

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        return self.tc.is_closing()

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self.tc.is_closed()

    @property
    def assumed_state(self):
        """Return True because covers can be stopped midway."""
        return True

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = kwargs[ATTR_POSITION]
            _LOGGER.debug('async_set_cover_position: %d', position)
            await self.set_position(position)

    async def async_close_cover(self, **kwargs):
        """Turn the device close."""
        _LOGGER.debug('async_close_cover')
        self.tc.start_travel_down()
        self._require_stop_cover = False
        self.start_auto_updater()
        await self._async_handle_command(SERVICE_CLOSE_COVER)

    async def async_open_cover(self, **kwargs):
        """Turn the device open."""
        _LOGGER.debug('async_open_cover')
        self.tc.start_travel_up()
        self._require_stop_cover = False
        self.start_auto_updater()
        await self._async_handle_command(SERVICE_OPEN_COVER)

    async def async_stop_cover(self, **kwargs):
        """Turn the device stop."""
        _LOGGER.debug('async_stop_cover')
        self._handle_my_button()
        await self._async_handle_command(SERVICE_STOP_COVER)

    async def set_position(self, position):
        _LOGGER.debug('set_position')
        """Move cover to a designated position."""
        current_position = self.shift_position(self.tc.current_position())
        _LOGGER.debug('set_position :: current_position: %d, new_position: %d',
                      current_position, position)
        command = None
        if position < current_position:
            command = SERVICE_CLOSE_COVER
        elif position > current_position:
            command = SERVICE_OPEN_COVER
        if command is not None:
            self._require_stop_cover = True
            self.start_auto_updater()
            self.tc.start_travel(self.shift_position(position))
            _LOGGER.debug('set_position :: command %s', command)
            await self._async_handle_command(command)
        return

    def start_auto_updater(self):
        """Start the autoupdater to update HASS while cover is moving."""
        _LOGGER.debug('start_auto_updater')
        if self._unsubscribe_auto_updater is None:
            _LOGGER.debug('init _unsubscribe_auto_updater')
#            self._unsubscribe_auto_updater = async_track_utc_time_change(self.hass, self.auto_updater_hook)
            interval = timedelta(seconds=0.1)
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self.auto_updater_hook, interval)

    @callback
    def auto_updater_hook(self, now):
        """Call for the autoupdater."""
        _LOGGER.debug('auto_updater_hook')
        self.async_write_ha_state()
        if self.position_reached():
            _LOGGER.debug('auto_updater_hook :: position_reached')
            self.stop_auto_updater()
        self.hass.async_create_task(self.auto_stop_if_necessary())

    def stop_auto_updater(self):
        """Stop the autoupdater."""
        _LOGGER.debug('stop_auto_updater')
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    def position_reached(self):
        """Return if cover has reached its final position."""
        return self.tc.position_reached()

    async def auto_stop_if_necessary(self):
        """Do auto stop if necessary."""
        # If device does not support auto_positioning,
        # we have to stop the device when position is reached.
        # unless device was traveling to fully open
        # or fully closed state
        if self.position_reached():
            if (
                    self._require_stop_cover and
                    not self.tc.is_closed() and
                    not self.tc.is_open()):
                _LOGGER.debug('auto_stop_if_necessary :: calling stop command')
                await self._async_handle_command(SERVICE_STOP_COVER)
            self.tc.stop()

    def shift_position(self, position):
        """Calculate 100 complement position"""
        try:
            return 100 - position
        except TypeError:
            return None

class InvertedRTSRflinkCover(RTSRflinkCover):
    """Rflink cover that has inverted open/close commands."""

    async def _async_send_command(self, cmd, repetitions):
        """Will invert only the UP/DOWN commands."""
        _LOGGER.debug("Getting command: %s for Rflink device: %s", cmd, self._device_id)
        cmd_inv = {"UP": "DOWN", "DOWN": "UP"}
        await super()._async_send_command(cmd_inv.get(cmd, cmd), repetitions)
