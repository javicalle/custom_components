"""Support for RTS Cover devices over RFLink controller."""
import logging
from datetime import timedelta

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.cover import (ATTR_CURRENT_POSITION,
                                            ATTR_POSITION, PLATFORM_SCHEMA,
                                            SUPPORT_CLOSE, SUPPORT_OPEN,
                                            SUPPORT_SET_POSITION, SUPPORT_STOP)
from homeassistant.components.rflink import (CONF_ALIASES,
                                             CONF_DEVICE_DEFAULTS,
                                             CONF_DEVICES, CONF_FIRE_EVENT,
                                             CONF_GROUP, CONF_GROUP_ALIASES,
                                             CONF_NOGROUP_ALIASES,
                                             CONF_SIGNAL_REPETITIONS,
                                             DEVICE_DEFAULTS_SCHEMA,
                                             EVENT_KEY_COMMAND)
from homeassistant.components.rflink.cover import RflinkCover
from homeassistant.const import (CONF_NAME, SERVICE_CLOSE_COVER,
                                 SERVICE_OPEN_COVER, SERVICE_STOP_COVER)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from xknx.devices import TravelCalculator, TravelStatus

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

CONF_MY_POSITION = "rts_my_position"
CONF_TRAVELLING_TIME_DOWN = "travelling_time_down"
CONF_TRAVELLING_TIME_UP = "travelling_time_up"
DEFAULT_TRAVEL_TIME = 25

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(
            CONF_DEVICE_DEFAULTS, default=DEVICE_DEFAULTS_SCHEMA({})
        ): DEVICE_DEFAULTS_SCHEMA,
        vol.Optional(CONF_DEVICES, default={}): vol.Schema(
            {
                cv.string: {
                    vol.Optional(CONF_NAME): cv.string,
                    vol.Optional(CONF_ALIASES, default=[]): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(CONF_GROUP_ALIASES, default=[]): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(CONF_NOGROUP_ALIASES, default=[]): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                    vol.Optional(CONF_FIRE_EVENT, default=False): cv.boolean,
                    vol.Optional(CONF_SIGNAL_REPETITIONS): vol.Coerce(int),
                    vol.Optional(CONF_GROUP, default=True): cv.boolean,
                    vol.Optional(CONF_MY_POSITION): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=100)
                    ),
                    vol.Optional(
                        CONF_TRAVELLING_TIME_DOWN, default=DEFAULT_TRAVEL_TIME
                    ): cv.positive_int,
                    vol.Optional(
                        CONF_TRAVELLING_TIME_UP, default=DEFAULT_TRAVEL_TIME
                    ): cv.positive_int,
                }
            }
        ),
    }
)


def devices_from_config(domain_config):
    """Parse configuration and add RFLink cover devices."""
    devices = []
    for device_id, config in domain_config[CONF_DEVICES].items():
        rts_my_position = config.pop(CONF_MY_POSITION)
        travel_time_down = config.pop(CONF_TRAVELLING_TIME_DOWN)
        travel_time_up = config.pop(CONF_TRAVELLING_TIME_UP)
        device_config = dict(domain_config[CONF_DEVICE_DEFAULTS], **config)
        device = RTSRflinkCover(
            device_id,
            rts_my_position,
            travel_time_down,
            travel_time_up,
            **device_config
        )
        devices.append(device)
    return devices


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the RFLink cover platform."""
    async_add_entities(devices_from_config(config))


class RTSRflinkCover(RflinkCover):
    """RFLink entity which can switch on/stop/off (eg: cover) with time based controls."""

    def __init__(
        self,
        device_id,
        rts_my_position,
        travel_time_down,
        travel_time_up,
        **device_config
    ):
        """Initialize the cover."""
        self._rts_my_position = rts_my_position
        self._travel_time_down = travel_time_down
        self._travel_time_up = travel_time_up

        #        self.async_register_callbacks()

        self._unsubscribe_auto_updater = None

        super().__init__(device_id, None, **device_config)

        self.travel_calc = TravelCalculator(
            self._travel_time_down, self._travel_time_up
        )

    async def async_added_to_hass(self):
        """Restore RFLink cover state (OPEN/CLOSE/CURRENT_POSITION)."""
        await super().async_added_to_hass()

        old_state = await self.async_get_last_state()
        if (
            old_state is not None
            and self.travel_calc is not None
            and old_state.attributes.get(ATTR_CURRENT_POSITION) is not None
        ):
            self.travel_calc.set_position(
                int(old_state.attributes.get(ATTR_CURRENT_POSITION))
            )

    def _handle_event(self, event):
        """Adjust state if RFLink picks up a remote command for this device."""
        self.cancel_queued_send_commands()
        _LOGGER.debug("_handle_event %s", event)

        # this must be wrong. ON command closes cover
        command = event.get(EVENT_KEY_COMMAND)
        if command in ["on", "allon", "up"]:
            self.travel_calc.start_travel_up()
            self.start_auto_updater()
        elif command in ["off", "alloff", "down"]:
            self.travel_calc.start_travel_down()
            self.start_auto_updater()
        elif command in ["stop"]:
            self._handle_my_button()

    def _handle_my_button(self):
        """Handle the MY button press."""
        if self.travel_calc.is_traveling():
            _LOGGER.debug("_handle_my_button :: button stops cover")
            self.travel_calc.stop()
            self.stop_auto_updater()
        elif self._rts_my_position is not None:
            _LOGGER.debug("_handle_my_button :: button sends to MY")
            self.travel_calc.start_travel(self._rts_my_position)
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
    def supported_features(self):
        """Flag supported features."""
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_SET_POSITION | SUPPORT_STOP

    @property
    def current_cover_position(self):
        """Return the current position of the cover."""
        return self.travel_calc.current_position()

    @property
    def is_opening(self):
        """Return if the cover is opening or not."""
        return (
            self.travel_calc.is_traveling()
            and self.travel_calc.travel_direction == TravelStatus.DIRECTION_UP
        )

    @property
    def is_closing(self):
        """Return if the cover is closing or not."""
        return (
            self.travel_calc.is_traveling()
            and self.travel_calc.travel_direction == TravelStatus.DIRECTION_DOWN
        )

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return self.travel_calc.is_closed()

    async def async_set_cover_position(self, **kwargs):
        """Move the cover to a specific position."""
        if ATTR_POSITION in kwargs:
            position = kwargs[ATTR_POSITION]
            _LOGGER.debug("async_set_cover_position: %d", position)
            await self.set_position(position)

    async def async_close_cover(self, **kwargs):
        """Turn the device close."""
        _LOGGER.debug("async_close_cover")
        await self._async_handle_command(SERVICE_CLOSE_COVER)
        self.travel_calc.start_travel_down()
        self.start_auto_updater()

    async def async_open_cover(self, **kwargs):
        """Turn the device open."""
        _LOGGER.debug("async_open_cover")
        await self._async_handle_command(SERVICE_OPEN_COVER)
        self.travel_calc.start_travel_up()
        self.start_auto_updater()

    async def async_stop_cover(self, **kwargs):
        """Turn the device stop."""
        _LOGGER.debug("async_stop_cover")
        await self._async_handle_command(SERVICE_STOP_COVER)
        self._handle_my_button()

    async def set_position(self, position):
        """Move cover to a designated position."""
        current_position = self.travel_calc.current_position()
        _LOGGER.debug(
            "set_position :: current_position: %d, new_position: %d",
            current_position,
            position,
        )
        command = None
        if position < current_position:
            command = SERVICE_CLOSE_COVER
        elif position > current_position:
            command = SERVICE_OPEN_COVER
        if command is not None:
            await self._async_handle_command(command)
            self.start_auto_updater()
            self.travel_calc.start_travel(position)
            _LOGGER.debug("set_position :: command %s", command)
        return

    def start_auto_updater(self):
        """Start the autoupdater to update HASS while cover is moving."""
        _LOGGER.debug("start_auto_updater")
        if self._unsubscribe_auto_updater is None:
            _LOGGER.debug("init _unsubscribe_auto_updater")
            interval = timedelta(seconds=0.1)
            self._unsubscribe_auto_updater = async_track_time_interval(
                self.hass, self.auto_updater_hook, interval
            )

    @callback
    def auto_updater_hook(self, now):
        """Call for the autoupdater."""
        _LOGGER.debug("auto_updater_hook")
        self.async_schedule_update_ha_state()
        if self.position_reached():
            _LOGGER.debug("auto_updater_hook :: position_reached")
            self.stop_auto_updater()
        self.hass.async_create_task(self.auto_stop_if_necessary())

    def stop_auto_updater(self):
        """Stop the autoupdater."""
        if self._unsubscribe_auto_updater is not None:
            self._unsubscribe_auto_updater()
            self._unsubscribe_auto_updater = None

    def position_reached(self):
        """Return if cover has reached its final position."""
        return self.travel_calc.position_reached()

    async def auto_stop_if_necessary(self):
        """Do auto stop if necessary."""
        # If device does not support auto_positioning,
        # we have to stop the device when position is reached.
        # unless device was traveling to fully open
        # or fully closed state
        current_position = self.travel_calc.current_position()
        if self.position_reached():
            self.travel_calc.stop()
            if 0 < current_position < 100:
                _LOGGER.debug("auto_stop_if_necessary :: calling stop command")
                await self._async_handle_command(SERVICE_STOP_COVER)
