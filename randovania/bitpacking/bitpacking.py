from __future__ import annotations

import dataclasses
import hashlib
import math
import struct
import typing
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

import bitstruct

from randovania.lib import type_lib

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from _typeshed import DataclassInstance, SupportsRichComparisonT

    from randovania.lib.json_lib import JsonObject, JsonObject_RO, JsonType, JsonType_RO

T = TypeVar("T")

ARBITRARY_INT_SIZE_LIMITS = (4, 16, 128, 1024)


def _bits_for_number(value: int) -> int:
    return int(math.ceil(math.log2(value)))


def single_byte_hash(data: bytes) -> int:
    return hashlib.blake2b(data, digest_size=1).digest()[0]


def _compile_format(*args: int) -> bitstruct.CompiledFormat:
    return bitstruct.CompiledFormat("".join(f"u{_bits_for_number(v)}" for v in args))


class BitPackDecoder:
    _data: bytes
    _offset: int

    def __init__(self, data: bytes):
        self._data = data
        self._offset = 0

    def decode(self, *args: int) -> tuple[int, ...]:
        """Decodes values from the current buffer, advancing the current pointer"""
        compiled = _compile_format(*args)
        offset = self._offset
        self._offset += compiled.calcsize()
        return compiled.unpack_from(self._data, offset)

    def decode_single(self, value: int) -> int:
        return self.decode(value)[0]

    def decode_element(self, array: Sequence[T]) -> T:
        if len(array) == 1:
            return array[0]
        return array[self.decode_single(len(array))]

    def peek(self, *args: int) -> tuple[int, ...]:
        """Decodes values from the current buffer, *NOT* advancing the current pointer"""
        compiled = _compile_format(*args)
        return compiled.unpack_from(self._data, self._offset)

    def ensure_data_end(self) -> None:
        try:
            self.peek(256)
            raise ValueError("At least one entire byte of data is still unread.")
        except bitstruct.Error:
            pass


class BitPackValue:
    def bit_pack_encode(self, metadata: dict) -> Iterator[tuple[int, int]]:
        raise NotImplementedError

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> typing.Self:
        raise NotImplementedError

    @classmethod
    def bit_pack_skip_if_equals(cls) -> bool:
        return True


class BitPackBool(BitPackValue):
    value: bool

    def __init__(self, value: bool):
        self.value = value

    def bit_pack_encode(self, metadata: dict) -> Iterator[tuple[int, int]]:
        yield from encode_bool(self.value)

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> bool:  # type: ignore[override]
        return decode_bool(decoder)

    @classmethod
    def bit_pack_skip_if_equals(cls) -> bool:
        return False


class BitPackFloat(BitPackValue):
    value: float

    def __init__(self, value: float):
        self.value = value

    def bit_pack_encode(self, metadata: dict) -> Iterator[tuple[int, int]]:
        if "if_different" in metadata:
            same = self.value == metadata["if_different"]
            yield from encode_bool(same)
            if same:
                return

        value_range = (metadata["max"] - metadata["min"]) * (10 ** metadata["precision"]) + 1
        yield int((self.value - metadata["min"]) * (10 ** metadata["precision"])), int(value_range)

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> float:  # type: ignore[override]
        if "if_different" in metadata:
            same = decode_bool(decoder)
            if same:
                return metadata["if_different"]

        value_range = (metadata["max"] - metadata["min"]) * (10 ** metadata["precision"]) + 1
        decoded = decoder.decode_single(int(value_range))
        return float((decoded / (10 ** metadata["precision"])) + metadata["min"])


class BitPackInt(BitPackValue):
    value: int

    def __init__(self, value: int):
        self.value = value

    def bit_pack_encode(self, metadata: dict) -> Iterator[tuple[int, int]]:
        if "if_different" in metadata:
            same = self.value == metadata["if_different"]
            yield from encode_bool(same)
            if same:
                return

        value_range = (metadata["max"] - metadata["min"]) + 1
        yield self.value - metadata["min"], value_range

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> int:  # type: ignore[override]
        if "if_different" in metadata:
            same = decode_bool(decoder)
            if same:
                return metadata["if_different"]

        value_range = (metadata["max"] - metadata["min"]) + 1
        decoded = decoder.decode_single(value_range)
        return decoded + metadata["min"]


class BitPackEnum(BitPackValue):
    def bit_pack_encode(self, metadata: dict) -> Iterator[tuple[int, int]]:
        assert isinstance(self, Enum)
        cls = self.__class__
        values = list(cls.__members__.values())
        yield from pack_array_element(self, values)

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> typing.Self:
        assert issubclass(cls, Enum)
        items = list(cls.__members__.values())
        return decoder.decode_element(items)


_default_bit_pack_classes: dict[type, type[BitPackValue]] = {
    bool: BitPackBool,
    int: BitPackInt,
    float: BitPackFloat,
}


def _get_bit_pack_value_for_type(value_type: type) -> type[BitPackValue]:
    if issubclass(value_type, BitPackValue):
        return value_type

    if value_type in _default_bit_pack_classes:
        return _default_bit_pack_classes[value_type]

    else:
        raise NotImplementedError(f"Unsupported bit packing for type {value_type}")


def _get_bit_pack_value_for(value: typing.Any, dataclass_type: type) -> BitPackValue:
    if isinstance(value, BitPackValue):
        return value

    return _get_bit_pack_value_for_type(dataclass_type)(value)  # type: ignore[call-arg]


class BitPackDataclass(BitPackValue):
    def bit_pack_encode(self: DataclassInstance, metadata: dict) -> Iterator[tuple[int, int]]:
        resolved_types = typing.get_type_hints(type(self))
        reference = metadata.get("reference")

        for field in dataclasses.fields(self):
            if not field.init:
                continue

            if field.metadata.get("manual_bitpacking"):
                continue

            item = getattr(self, field.name)
            field_type = resolved_types[field.name]

            resolved_type, optional = type_lib.resolve_optional(field_type)
            if optional:
                yield from encode_bool(item is not None)
                if item is None:
                    continue

            bit_pack_value = _get_bit_pack_value_for(item, resolved_type)

            if reference is not None:
                reference_item = getattr(reference, field.name)
            else:
                reference_item = None

            field_meta = dict(**field.metadata)
            field_meta["reference"] = reference_item

            encoded_item = list(bit_pack_value.bit_pack_encode(field_meta))
            if any(not (0 <= a < b) for (a, b) in encoded_item):
                raise ValueError(
                    f"Encoding field {field.name} of {type(self)} generated invalid value: {encoded_item}."
                )
            should_encode = True

            if bit_pack_value.bit_pack_skip_if_equals() and reference_item is not None:
                # reference_item being None means: no reference, or it's None in reference.
                # for second case, if we reached this point `item` is not None, so it's obviously different

                reference_pack_value = _get_bit_pack_value_for(reference_item, resolved_type)
                encoded_reference = list(reference_pack_value.bit_pack_encode(field_meta))
                should_encode = encoded_item != encoded_reference
                yield from encode_bool(should_encode)

            if should_encode:
                yield from encoded_item

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> typing.Self:
        resolved_types = typing.get_type_hints(cls)
        reference = metadata.get("reference")
        args = {}

        dc: type[DataclassInstance] = cls  # type: ignore[assignment]

        for field in dataclasses.fields(dc):
            if not field.init:
                continue

            if field.metadata.get("manual_bitpacking"):
                args[field.name] = metadata["extra_args"][field.name]
                continue

            resolved_type, optional = type_lib.resolve_optional(resolved_types[field.name])

            if optional:
                should_decode = decode_bool(decoder)
            else:
                should_decode = True

            item = None
            bit_pack_value = _get_bit_pack_value_for_type(resolved_type)

            if reference is not None and should_decode:
                reference_item = getattr(reference, field.name)
                if bit_pack_value.bit_pack_skip_if_equals() and not decode_bool(decoder):
                    item = reference_item
                    should_decode = False
            else:
                reference_item = None

            if should_decode:
                field_meta = dict(**field.metadata)
                field_meta["reference"] = reference_item
                item = bit_pack_value.bit_pack_unpack(decoder, field_meta)

            args[field.name] = item

        return cls(**args)


class BitPackJson(BitPackValue):
    """
    BitPackValue that operates on valid JSON objects. Less efficient than manual
    dedicated bitpacking, but gets the job done for stuff like `extra` dicts
    """

    class JsonTypes(BitPackEnum, Enum):
        ARRAY = 0
        BOOLEAN = 1
        NULL = 2
        INTEGER = 3
        NUMBER = 4
        OBJECT = 5
        STRING = 6

    value: JsonObject_RO

    def __init__(self, value: JsonObject_RO):
        self.value = value

    def bit_pack_encode(self, metadata: dict) -> Iterator[tuple[int, int]]:
        yield from encode_big_int(len(self.value))

        for key, value in self.value.items():
            yield from encode_string(key)
            yield from self.encode_json_value(value)

    def encode_json_value(self, value: JsonType_RO) -> Iterator[tuple[int, int]]:
        encoded = typing.cast("Iterator[tuple[int, int]]", ())

        if isinstance(value, str):
            value_type = BitPackJson.JsonTypes.STRING
            encoded = encode_string(value)

        elif isinstance(value, bool):
            value_type = BitPackJson.JsonTypes.BOOLEAN
            encoded = encode_bool(value)

        elif value is None:
            value_type = BitPackJson.JsonTypes.NULL

        elif isinstance(value, int):
            value_type = BitPackJson.JsonTypes.INTEGER
            encoded = encode_big_int(value)

        elif isinstance(value, float):
            value_type = BitPackJson.JsonTypes.NUMBER
            encoded = encode_float_as_bytes(value)

        elif isinstance(value, Sequence):
            value_type = BitPackJson.JsonTypes.ARRAY
            encoded = encode_tuple(value, self.encode_json_value)

        elif isinstance(value, Mapping):
            value_type = BitPackJson.JsonTypes.OBJECT
            encoded = BitPackJson(value).bit_pack_encode({})

        else:
            raise TypeError(f"Encoding error in bitpacking: value of type {type(value)} is not a JSON value")

        yield from value_type.bit_pack_encode({})
        yield from encoded

    @classmethod
    def bit_pack_unpack(cls, decoder: BitPackDecoder, metadata: dict) -> JsonObject:  # type: ignore[override]
        result: JsonObject = {}

        size = decode_big_int(decoder)
        for _ in range(size):
            key = decode_string(decoder)
            result[key] = cls.decode_json_value(decoder)

        return result

    @classmethod
    def decode_json_value(cls, decoder: BitPackDecoder) -> JsonType:
        value_type = BitPackJson.JsonTypes.bit_pack_unpack(decoder, {})

        match value_type:
            case BitPackJson.JsonTypes.STRING:
                return decode_string(decoder)

            case BitPackJson.JsonTypes.BOOLEAN:
                return decode_bool(decoder)

            case BitPackJson.JsonTypes.NULL:
                return None

            case BitPackJson.JsonTypes.INTEGER:
                return decode_big_int(decoder)

            case BitPackJson.JsonTypes.NUMBER:
                return decode_float_as_bytes(decoder)

            case BitPackJson.JsonTypes.ARRAY:
                return list(decode_tuple(decoder, cls.decode_json_value))

            case BitPackJson.JsonTypes.OBJECT:
                return BitPackJson.bit_pack_unpack(decoder, {})


def pack_array_element(element: T, array: list[T]) -> Iterator[tuple[int, int]]:
    if len(array) > 1:
        yield array.index(element), len(array)
    else:
        if element not in array:
            raise ValueError(f"given element {element} is not in array of {len(array)}")


def _is_sorted(array: list[SupportsRichComparisonT]) -> bool:
    return array == sorted(array)


def _limits_for_size(remaining_size: int) -> tuple[int, ...]:
    if remaining_size > 4:
        return 4, remaining_size
    elif remaining_size > 0:
        return (remaining_size,)
    else:
        return ()


def _aux_pack_sorted_array_elements(elements: list[T], array: list[T]) -> Iterator[tuple[int, int]]:
    yield len(elements), len(array) + 1

    previous_index = 0
    for i, item in enumerate(elements):
        index = array.index(item)
        assert index is not None

        remaining_size = len(array) - previous_index - (len(elements) - i)
        yield from encode_int_with_limits(index - previous_index, _limits_for_size(remaining_size))
        previous_index = index


def pack_sorted_array_elements(
    elements: list[SupportsRichComparisonT],
    array: list[SupportsRichComparisonT],
) -> Iterator[tuple[int, int]]:
    # elements must be sorted so the N-th element is smaller than N+1-th
    assert _is_sorted(elements)
    assert _is_sorted(array)
    assert len(array) == len(set(array))

    if not array:
        return

    inverted_elements = [item for item in array if item not in elements]

    normal_results = list(_aux_pack_sorted_array_elements(elements, array))
    inverted_results = list(_aux_pack_sorted_array_elements(inverted_elements, array))

    bits_for_normal = sum(_bits_for_number(v[1]) for v in normal_results)
    bits_for_inverted = sum(_bits_for_number(v[1]) for v in inverted_results)

    should_invert = bits_for_normal > bits_for_inverted
    yield from encode_bool(should_invert)
    if should_invert:
        yield from inverted_results
    else:
        yield from normal_results


def decode_sorted_array_elements[T](decoder: BitPackDecoder, array: list[T]) -> list[T]:
    result = []

    if not array:
        return []

    inverted = decode_bool(decoder)
    elements_size = decoder.decode_single(len(array) + 1)

    previous_index = 0
    for i in range(elements_size):
        remaining_size = len(array) - previous_index - (elements_size - i)
        index = decode_int_with_limits(decoder, _limits_for_size(remaining_size))

        previous_index += index
        result.append(array[previous_index])

    if inverted:
        return [item for item in array if item not in result]
    else:
        return result


def encode_int_with_limits(value: int, limits: tuple[int, ...]) -> Iterator[tuple[int, int]]:
    previous_limit_sum = 0

    for i, limit in enumerate(limits):
        limit -= previous_limit_sum
        previous_limit_sum += limit

        if i == len(limits) - 1 or value < limit:
            yield value, limit + 1
            break

        yield limit, limit + 1
        value -= limit


def decode_int_with_limits(decoder: BitPackDecoder, limits: tuple[int, ...]) -> int:
    value = 0
    previous_limit_sum = 0

    for limit in limits:
        limit -= previous_limit_sum
        previous_limit_sum += limit

        new_value = decoder.decode_single(limit + 1)
        value += new_value
        if new_value < limit:
            break

    return value


def encode_big_int(i: int) -> Iterator[tuple[int, int]]:
    yield from encode_int_with_limits(i, ARBITRARY_INT_SIZE_LIMITS)


def decode_big_int(decoder: BitPackDecoder) -> int:
    return decode_int_with_limits(decoder, ARBITRARY_INT_SIZE_LIMITS)


def encode_bytes(b: bytes) -> Iterator[tuple[int, int]]:
    yield from encode_big_int(len(b))
    for item in b:
        yield item, 256


def decode_bytes(decoder: BitPackDecoder) -> bytes:
    size = decode_big_int(decoder)
    return bytes(decoder.decode(*([256] * size)))


def encode_float_as_bytes(f: float) -> Iterator[tuple[int, int]]:
    packed = struct.pack("<d", f)
    return encode_bytes(packed)


def decode_float_as_bytes(decoder: BitPackDecoder) -> float:
    packed = decode_bytes(decoder)
    return struct.unpack("<d", packed)[0]


def encode_string(s: str) -> Iterator[tuple[int, int]]:
    yield from encode_bytes(s.encode("utf-8"))


def decode_string(decoder: BitPackDecoder) -> str:
    return decode_bytes(decoder).decode("utf-8")


def encode_bool(value: bool) -> Iterator[tuple[int, int]]:
    yield int(bool(value)), 2


def decode_bool(decoder: BitPackDecoder) -> bool:
    return bool(decoder.decode_single(2))


def encode_tuple(value: Sequence[T], encoder: Callable[[T], Iterator[tuple[int, int]]]) -> Iterator[tuple[int, int]]:
    yield from encode_big_int(len(value))
    for it in value:
        yield from encoder(it)


def decode_tuple[T](decoder: BitPackDecoder, item_decoder: Callable[[BitPackDecoder], T]) -> tuple[T, ...]:
    size = decode_big_int(decoder)
    return tuple(item_decoder(decoder) for _ in range(size))


def _format_string_for(values: list[tuple[int, int]]) -> str:
    return "".join(f"u{_bits_for_number(v)}" for _, v in values)


def _pack_encode_results(values: list[tuple[int, int]]) -> bytes:
    f = _format_string_for(values)
    return bitstruct.compile(f).pack(*[argument for argument, _ in values])


def pack_results_and_bit_count(it: Iterator[tuple[int, int]]) -> tuple[bytes, int]:
    values = list(it)
    bit_count = sum(_bits_for_number(v) for _, v in values)
    return _pack_encode_results(values), bit_count


def pack_value(value: BitPackValue, metadata: dict | None = None) -> bytes:
    if metadata is None:
        metadata = {}

    results = []

    for i, (value_argument, value_format) in enumerate(value.bit_pack_encode(metadata)):
        if 0 <= value_argument < value_format:
            results.append((value_argument, value_format))
        else:
            raise ValueError(f"At {i}, got {value_argument} which not in range [0, {value_format}[")

    return _pack_encode_results(results)


BoundTypeValue = typing.TypeVar("BoundTypeValue", bound=BitPackValue)


def round_trip[BoundTypeValue: BitPackValue](value: BoundTypeValue, metadata: dict | None = None) -> BoundTypeValue:
    """
    Encodes the given value and then recreates it using the encoded value
    :param value:
    :param metadata:
    :return:
    """
    if metadata is None:
        metadata = {}

    b = pack_value(value, metadata)
    decoder = BitPackDecoder(b)
    return value.__class__.bit_pack_unpack(decoder, metadata)
