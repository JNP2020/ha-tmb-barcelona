"""Config flow for TMB.

One config entry acts as a single "hub" holding the `app_id`/`app_key`
credentials; the list of monitored (mode, line, stop) items lives in the
entry's options and is grown or shrunk afterwards via the options flow
(Configure -> add/remove stop), mirroring how the entry's first stop is
picked during initial setup: choose a mode, then a line, then a stop on
that line.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import LineInfo, StopInfo, TmbApiClient, TmbApiError, TmbAuthError
from .const import (
    CONF_APP_ID,
    CONF_APP_KEY,
    CONF_LINE_CODE,
    CONF_LINE_NAME,
    CONF_MODE,
    CONF_STOP_CODE,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    MODE_BUS,
    MODE_METRO,
)
from .coordinator import item_key

MODE_OPTIONS = [
    SelectOptionDict(value=MODE_BUS, label="Bus"),
    SelectOptionDict(value=MODE_METRO, label="Metro"),
]


def _mode_schema(default: str = MODE_BUS) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MODE, default=default): SelectSelector(
                SelectSelectorConfig(options=MODE_OPTIONS, mode=SelectSelectorMode.LIST)
            )
        }
    )


def _line_schema(lines: list[LineInfo]) -> vol.Schema:
    options = [
        SelectOptionDict(value=line["code"], label=f"{line['name']} ({line['code']})")
        for line in lines
    ]
    return vol.Schema(
        {
            vol.Required(CONF_LINE_CODE): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        }
    )


def _stop_schema(stops: list[StopInfo]) -> vol.Schema:
    options = [
        SelectOptionDict(value=stop["code"], label=f"{stop['name']} ({stop['code']})")
        for stop in stops
    ]
    return vol.Schema(
        {
            vol.Required(CONF_STOP_CODE): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
            )
        }
    )


async def _async_get_lines(client: TmbApiClient, mode: str) -> list[LineInfo]:
    if mode == MODE_BUS:
        return await client.async_get_bus_lines()
    return await client.async_get_metro_lines()


async def _async_get_stops(client: TmbApiClient, mode: str, line_code: str) -> list[StopInfo]:
    if mode == MODE_BUS:
        return await client.async_get_bus_line_stops(line_code)
    return await client.async_get_metro_line_stations(line_code)


async def _async_validate_credentials(hass, app_id: str, app_key: str) -> str | None:
    """Try the credentials against the API; return an error code, or None."""
    client = TmbApiClient(async_get_clientsession(hass), app_id, app_key)
    try:
        await client.async_get_bus_lines()
    except TmbAuthError:
        return "invalid_auth"
    except TmbApiError:
        return "cannot_connect"
    return None


class TmbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup: validate credentials, pick the first stop."""

    VERSION = 1

    def __init__(self) -> None:
        self._app_id: str | None = None
        self._app_key: str | None = None
        self._mode: str | None = None
        self._line_code: str | None = None
        self._line_name: str | None = None
        self._lines_cache: list[LineInfo] = []
        self._reauth_entry: ConfigEntry | None = None

    def _client(self) -> TmbApiClient:
        return TmbApiClient(async_get_clientsession(self.hass), self._app_id, self._app_key)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            error = await _async_validate_credentials(
                self.hass, user_input[CONF_APP_ID], user_input[CONF_APP_KEY]
            )
            if error:
                errors["base"] = error
            else:
                self._app_id = user_input[CONF_APP_ID]
                self._app_key = user_input[CONF_APP_KEY]
                return await self.async_step_mode()

        schema = vol.Schema(
            {vol.Required(CONF_APP_ID): str, vol.Required(CONF_APP_KEY): str}
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if user_input is not None:
            self._mode = user_input[CONF_MODE]
            return await self.async_step_line()

        return self.async_show_form(step_id="mode", data_schema=_mode_schema())

    async def async_step_line(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}
        self._lines_cache = await _async_get_lines(self._client(), self._mode)

        if user_input is not None:
            self._line_code = user_input[CONF_LINE_CODE]
            self._line_name = next(
                (line["name"] for line in self._lines_cache if line["code"] == self._line_code),
                self._line_code,
            )
            return await self.async_step_stop()

        return self.async_show_form(
            step_id="line", data_schema=_line_schema(self._lines_cache), errors=errors
        )

    async def async_step_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        stops = await _async_get_stops(self._client(), self._mode, self._line_code)

        if user_input is not None:
            stop_code = user_input[CONF_STOP_CODE]
            stop_name = next(
                (stop["name"] for stop in stops if stop["code"] == stop_code), stop_code
            )
            item = {
                CONF_MODE: self._mode,
                CONF_LINE_CODE: self._line_code,
                CONF_LINE_NAME: self._line_name,
                CONF_STOP_CODE: stop_code,
                CONF_STOP_NAME: stop_name,
            }
            return self.async_create_entry(
                title="TMB",
                data={CONF_APP_ID: self._app_id, CONF_APP_KEY: self._app_key},
                options={CONF_STOPS: [item]},
            )

        return self.async_show_form(step_id="stop", data_schema=_stop_schema(stops))

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> dict[str, Any]:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _async_validate_credentials(
                self.hass, user_input[CONF_APP_ID], user_input[CONF_APP_KEY]
            )
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_APP_ID: user_input[CONF_APP_ID],
                        CONF_APP_KEY: user_input[CONF_APP_KEY],
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {vol.Required(CONF_APP_ID): str, vol.Required(CONF_APP_KEY): str}
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "TmbOptionsFlow":
        return TmbOptionsFlow()


class TmbOptionsFlow(OptionsFlow):
    """Add or remove monitored stops, or update credentials."""

    def __init__(self) -> None:
        self._mode: str | None = None
        self._line_code: str | None = None
        self._line_name: str | None = None

    def _client(self) -> TmbApiClient:
        return TmbApiClient(
            async_get_clientsession(self.hass),
            self.config_entry.data[CONF_APP_ID],
            self.config_entry.data[CONF_APP_KEY],
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_stop", "remove_stop", "credentials"],
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _async_validate_credentials(
                self.hass, user_input[CONF_APP_ID], user_input[CONF_APP_KEY]
            )
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_APP_ID: user_input[CONF_APP_ID],
                        CONF_APP_KEY: user_input[CONF_APP_KEY],
                    },
                )
                return self.async_create_entry(title="", data=self.config_entry.options)

        schema = vol.Schema(
            {
                vol.Required(CONF_APP_ID, default=self.config_entry.data[CONF_APP_ID]): str,
                vol.Required(CONF_APP_KEY, default=self.config_entry.data[CONF_APP_KEY]): str,
            }
        )
        return self.async_show_form(step_id="credentials", data_schema=schema, errors=errors)

    async def async_step_add_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if user_input is not None:
            self._mode = user_input[CONF_MODE]
            return await self.async_step_add_stop_line()

        return self.async_show_form(step_id="add_stop", data_schema=_mode_schema())

    async def async_step_add_stop_line(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        lines = await _async_get_lines(self._client(), self._mode)

        if user_input is not None:
            self._line_code = user_input[CONF_LINE_CODE]
            self._line_name = next(
                (line["name"] for line in lines if line["code"] == self._line_code),
                self._line_code,
            )
            return await self.async_step_add_stop_confirm()

        return self.async_show_form(step_id="add_stop_line", data_schema=_line_schema(lines))

    async def async_step_add_stop_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        stops = await _async_get_stops(self._client(), self._mode, self._line_code)

        if user_input is not None:
            stop_code = user_input[CONF_STOP_CODE]
            stop_name = next(
                (stop["name"] for stop in stops if stop["code"] == stop_code), stop_code
            )
            new_item = {
                CONF_MODE: self._mode,
                CONF_LINE_CODE: self._line_code,
                CONF_LINE_NAME: self._line_name,
                CONF_STOP_CODE: stop_code,
                CONF_STOP_NAME: stop_name,
            }
            current = list(self.config_entry.options.get(CONF_STOPS, []))
            if item_key(new_item) not in {item_key(item) for item in current}:
                current.append(new_item)
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_STOPS: current}
            )

        return self.async_show_form(
            step_id="add_stop_confirm", data_schema=_stop_schema(stops)
        )

    async def async_step_remove_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        current = self.config_entry.options.get(CONF_STOPS, [])
        if not current:
            return self.async_abort(reason="no_stops_configured")

        if user_input is not None:
            to_remove = set(user_input[CONF_STOP_CODE])
            updated = [item for item in current if item_key(item) not in to_remove]
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_STOPS: updated}
            )

        options = [
            SelectOptionDict(
                value=item_key(item),
                label=(
                    f"{item[CONF_LINE_NAME]} ({item[CONF_LINE_CODE]}) - "
                    f"{item[CONF_STOP_NAME]} [{item[CONF_MODE]}]"
                ),
            )
            for item in current
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_STOP_CODE, default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                        multiple=True,
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove_stop", data_schema=schema)
