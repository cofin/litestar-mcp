"""JSON wire encoding shared by the MCP and A2A transports.

Adapted from ``sqlspec.utils.serializers``: msgspec encodes with a
type-keyed encoder registry walked along the value's MRO, structural
fallbacks cover dataclasses, attrs classes, and msgspec Structs, and the
standard library serializer is used only when msgspec cannot encode a value.
The registry mirrors msgspec's native output so both paths agree.
"""

import base64
import dataclasses
import datetime
import enum
import functools
import json
import logging
import uuid
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, Literal, Protocol, overload

from msgspec.json import Decoder, Encoder

from litestar_mcp.core._typing import ATTRS_INSTALLED, PYDANTIC_INSTALLED, BaseModel
from litestar_mcp.utils.type_guards import is_attrs_instance, is_msgspec_struct

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = (
    "DEFAULT_TYPE_ENCODERS",
    "JSONSerializer",
    "MsgspecSerializer",
    "StandardLibSerializer",
    "TypeEncodersMap",
    "decode_json",
    "encode_json",
    "get_default_serializer",
)

TypeEncodersMap = "Mapping[type, Callable[[Any], Any]]"
_logger = logging.getLogger(__name__)


def _datetime_to_gmt_iso(value: "datetime.datetime") -> "str":
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _dump_pydantic_model(value: "Any") -> "Any":
    return value.model_dump(mode="json")


def _default_type_encoders() -> "dict[type, Callable[[Any], Any]]":
    encoders: dict[type, Callable[[Any], Any]] = {
        datetime.datetime: _datetime_to_gmt_iso,
        datetime.date: lambda value: value.isoformat(),
        datetime.time: lambda value: value.isoformat(),
        datetime.timedelta: lambda value: value.total_seconds(),
        Decimal: str,
        uuid.UUID: str,
        Path: str,
        PurePath: str,
        IPv4Address: str,
        IPv4Interface: str,
        IPv4Network: str,
        IPv6Address: str,
        IPv6Interface: str,
        IPv6Network: str,
        frozenset: list,
        set: list,
        bytes: lambda value: base64.b64encode(value).decode("ascii"),
        enum.Enum: lambda value: value.value,
    }
    if PYDANTIC_INSTALLED:
        encoders[BaseModel] = _dump_pydantic_model
    return encoders


DEFAULT_TYPE_ENCODERS: "dict[type, Callable[[Any], Any]]" = _default_type_encoders()
_STRUCTURAL_ENCODER_CACHE: "dict[type[Any], Callable[[Any], Any] | None]" = {}


def _dump_dataclass(value: "Any") -> "dict[str, Any]":
    return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}


def _dump_attrs_instance(value: "Any") -> "Any":
    from attrs import asdict

    return asdict(value, recurse=True)


def _dump_msgspec_struct(value: "Any") -> "dict[str, Any]":
    return {field_name: getattr(value, field_name) for field_name in value.__struct_fields__}


def _structural_encoder(value: "Any") -> "Any":
    value_type = type(value)
    if value_type in _STRUCTURAL_ENCODER_CACHE:
        encoder = _STRUCTURAL_ENCODER_CACHE[value_type]
        return None if encoder is None else encoder(value)
    encoder = None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        encoder = _dump_dataclass
    elif ATTRS_INSTALLED and is_attrs_instance(value):
        encoder = _dump_attrs_instance
    elif is_msgspec_struct(value):
        encoder = _dump_msgspec_struct
    _STRUCTURAL_ENCODER_CACHE[value_type] = encoder
    return None if encoder is None else encoder(value)


def _enc_hook(type_encoders: "Mapping[type, Callable[[Any], Any]]") -> "Callable[[Any], Any]":
    def enc_hook(value: "Any") -> "Any":
        for base in value.__class__.__mro__[:-1]:
            encoder = type_encoders.get(base)
            if encoder is not None:
                return encoder(value)
        structural = _structural_encoder(value)
        if structural is not None:
            return structural
        msg = f"unsupported JSON value: {type(value).__name__}"
        raise TypeError(msg)

    return enc_hook


_DEFAULT_ENC_HOOK: "Callable[[Any], Any]" = _enc_hook(DEFAULT_TYPE_ENCODERS)


def _merge_type_encoders(type_encoders: "Mapping[type, Callable[[Any], Any]] | None") -> "Callable[[Any], Any]":
    if not type_encoders:
        return _DEFAULT_ENC_HOOK
    return _enc_hook({**DEFAULT_TYPE_ENCODERS, **type_encoders})


def _is_explicit_unsupported_error(exc: "Exception") -> "bool":
    return "unsupported json value" in str(exc).lower()


class JSONSerializer(Protocol):
    """Protocol for JSON serializer implementations."""

    def encode(self, data: "Any", *, as_bytes: "bool" = False) -> "str | bytes":
        """Encode Python data into JSON."""
        ...

    def decode(self, data: "str | bytes") -> "Any":
        """Decode JSON into Python data."""
        ...


class StandardLibSerializer:
    """Standard library JSON serializer used when msgspec cannot encode a value."""

    __slots__ = ("_enc_hook",)

    def __init__(self, type_encoders: "Mapping[type, Callable[[Any], Any]] | None" = None) -> "None":
        self._enc_hook = _merge_type_encoders(type_encoders)

    def encode(self, data: "Any", *, as_bytes: "bool" = False) -> "str | bytes":
        encoded = json.dumps(data, default=self._enc_hook, separators=(",", ":"))
        return encoded.encode("utf-8") if as_bytes else encoded

    def decode(self, data: "str | bytes") -> "Any":
        return json.loads(data)


class MsgspecSerializer:
    """msgspec JSON serializer with the shared type encoder registry."""

    __slots__ = ("_decoder", "_enc_hook", "_encoder", "_fallback")

    def __init__(self, type_encoders: "Mapping[type, Callable[[Any], Any]] | None" = None) -> "None":
        self._enc_hook = _merge_type_encoders(type_encoders)
        self._encoder = Encoder(enc_hook=self._enc_hook)
        self._decoder = Decoder()
        self._fallback = StandardLibSerializer(type_encoders)

    def encode(self, data: "Any", *, as_bytes: "bool" = False) -> "str | bytes":
        try:
            encoded = self._encoder.encode(data)
        except TypeError as exc:
            if _is_explicit_unsupported_error(exc):
                raise
            _logger.debug("msgspec JSON encode failed with %s; falling back to the standard library", exc)
            return self._fallback.encode(data, as_bytes=as_bytes)
        except ValueError as exc:
            _logger.debug("msgspec JSON encode failed with %s; falling back to the standard library", exc)
            return self._fallback.encode(data, as_bytes=as_bytes)
        return encoded if as_bytes else encoded.decode("utf-8")

    def decode(self, data: "str | bytes") -> "Any":
        payload = data.encode("utf-8") if isinstance(data, str) else data
        try:
            return self._decoder.decode(payload)
        except (TypeError, ValueError):
            return self._fallback.decode(payload)


@functools.lru_cache(maxsize=1)
def get_default_serializer() -> "JSONSerializer":
    """Return the process-wide default JSON serializer."""
    return MsgspecSerializer()


@overload
def encode_json(data: "Any", *, as_bytes: "Literal[False]" = ...) -> "str": ...


@overload
def encode_json(data: "Any", *, as_bytes: "Literal[True]") -> "bytes": ...


def encode_json(data: "Any", *, as_bytes: "bool" = False) -> "str | bytes":
    """Encode Python data into JSON text, or bytes when ``as_bytes`` is set."""
    return get_default_serializer().encode(data, as_bytes=as_bytes)


def decode_json(data: "str | bytes") -> "Any":
    """Decode JSON text or bytes into Python data.

    Raises:
        ValueError: If the payload is not valid JSON.
    """
    return get_default_serializer().decode(data)
