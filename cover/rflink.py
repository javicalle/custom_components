"""
Support for Rflink Cover devices.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/cover.rflink/
"""
import logging

import voluptuous as vol

from homeassistant.components.rflink import (
    DEVICE_DEFAULTS_SCHEMA, RflinkCommand)
from homeassistant.components.cover import (
    CoverDevice, PLATFORM_SCHEMA)
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME


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
        },
    }),
})


def devices_from_config(domain_config, hass=None):
    """Parse configuration and add Rflink cover devices."""
    devices = []
    for device_id, config in domain_config[CONF_DEVICES].items():
        device_config = dict(domain_config[CONF_DEVICE_DEFAULTS], **config)
        device = RflinkCover(device_id, hass, **device_config)
        devices.append(device)

    return devices


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up the Rflink cover platform."""
    async_add_entities(devices_from_config(config, hass))


class RflinkCover(RflinkCommand, CoverDevice):
    """Rflink entity which can switch on/stop/off (eg: cover)."""

    def _handle_event(self, event):
        """Adjust state if Rflink picks up a remote command for this device."""
        self.cancel_queued_send_commands()

        command = event['command']
        if command in ['on', 'allon', 'up']:
            self._state = True
        elif command in ['off', 'alloff', 'down']:
            self._state = False

    @property
    def should_poll(self):
        """No polling available in RFlink cover."""
        return False

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        return not self._state

    @property
    def assumed_state(self):
        """Return True because covers can be stopped midway."""
        return True

    def async_close_cover(self, **kwargs):
        """Turn the device close."""
        return self._async_handle_command("close_cover")

    def async_open_cover(self, **kwargs):
        """Turn the device open."""
        return self._async_handle_command("open_cover")

    def async_stop_cover(self, **kwargs):
        """Turn the device stop."""
        return self._async_handle_command("stop_cover")
