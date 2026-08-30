"""A small, dependency-free TOML reader and writer.

This module intentionally implements a focused subset of TOML: standard and
quoted keys, tables, strings, integers, floating-point numbers, booleans, and
arrays containing supported values.  Multiline strings, dates and times,
inline tables, arrays of tables, and other advanced TOML features are outside
its scope.
"""

import json
import re


class TOMLDecodeError(ValueError):
    pass


_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_INTEGER = re.compile(r"^[+-]?[0-9][0-9_]*$")
_FLOAT = re.compile(
    r"^[+-]?(?:[0-9][0-9_]*\.[0-9_]+|[0-9][0-9_]*[eE][+-]?[0-9_]+)$"
)


def _outside(text, wanted):
    """Locate a character that is not nested inside a string or array.

    Args:
        text: The source text to scan.
        wanted: The single character to locate.

    Returns:
        The zero-based position of the first matching character outside a
        quoted string or array, or ``-1`` when no such character exists.
    """
    quote = None
    escaped = False
    depth = 0
    for pos, char in enumerate(text):
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == wanted and depth == 0:
            return pos
    return -1


def _strip_comment(line):
    """Remove a TOML comment from one physical line.

    A hash character inside a quoted string or array value is preserved.

    Args:
        line: A single line of TOML source text.

    Returns:
        The portion of ``line`` before an unquoted comment marker, or the
        original line if it contains no comment.
    """
    pos = _outside(line, "#")
    return line if pos < 0 else line[:pos]


def _key(text, line_number):
    """Parse a bare, basic quoted, or literal quoted TOML key.

    Args:
        text: Source text containing exactly one key.
        line_number: One-based source line number used in error messages.

    Returns:
        The decoded key as a string.

    Raises:
        TOMLDecodeError: If ``text`` is not a supported, valid key.
    """
    text = text.strip()
    if _BARE_KEY.match(text):
        return text
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            value = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise TOMLDecodeError(f"invalid quoted key on line {line_number}") from exc
        if isinstance(value, str):
            return value
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    raise TOMLDecodeError(f"invalid key on line {line_number}")


def _split_array(text, line_number):
    """Parse the contents of a TOML array.

    Commas inside quoted strings or nested arrays are not treated as item
    separators.  Each item is decoded by :func:`_value`.

    Args:
        text: Text between the array's opening and closing brackets.
        line_number: One-based source line number used in error messages.

    Returns:
        A list containing the decoded array values.

    Raises:
        TOMLDecodeError: If any array item is not a supported TOML value.
    """
    values = []
    start = 0
    quote = None
    escaped = False
    depth = 0
    for pos, char in enumerate(text):
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            item = text[start:pos].strip()
            if item:
                values.append(_value(item, line_number))
            start = pos + 1
    item = text[start:].strip()
    if item:
        values.append(_value(item, line_number))
    return values


def _value(text, line_number):
    """Decode one value from the supported TOML subset.

    Args:
        text: Source text for a string, number, boolean, or array.
        line_number: One-based source line number used in error messages.

    Returns:
        A ``str``, ``int``, ``float``, ``bool``, or ``list`` representing the
        decoded value.

    Raises:
        TOMLDecodeError: If the value is malformed or uses an unsupported TOML
            type.
    """
    text = text.strip()
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise TOMLDecodeError(f"invalid string on line {line_number}") from exc
        if isinstance(value, str):
            return value
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    if len(text) >= 2 and text[0] == "[" and text[-1] == "]":
        return _split_array(text[1:-1], line_number)
    if text == "true":
        return True
    if text == "false":
        return False
    number = text.replace("_", "")
    if _INTEGER.match(text):
        return int(number)
    if _FLOAT.match(text):
        return float(number)
    raise TOMLDecodeError(f"unsupported value on line {line_number}")


def loads(text):
    """Deserialize TOML text into a dictionary.

    Args:
        text: TOML source as a Unicode string.

    Returns:
        A dictionary containing decoded values and nested dictionaries for
        tables.

    Raises:
        TypeError: If ``text`` is not a string.
        TOMLDecodeError: If the input is malformed or contains unsupported
            TOML syntax or value types.
    """
    if not isinstance(text, str):
        raise TypeError("loads() expects a string")
    # TOML arrays may span physical lines. Join them before parsing while
    # preserving the first line number for useful diagnostics.
    logical_lines = []
    pending = []
    pending_line = 0
    depth = 0
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        stripped = _strip_comment(raw_line)
        if not pending and not stripped.strip():
            continue
        if not pending:
            pending_line = line_number
        pending.append(stripped)
        quote = None
        escaped = False
        for char in stripped:
            if quote:
                if quote == '"' and escaped:
                    escaped = False
                elif quote == '"' and char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
        if depth < 0:
            raise TOMLDecodeError(f"unexpected closing bracket on line {line_number}")
        if depth == 0:
            logical_lines.append((pending_line, " ".join(pending)))
            pending = []
    if pending:
        raise TOMLDecodeError(f"unterminated array on line {pending_line}")

    result = {}
    current = result
    for line_number, raw_line in logical_lines:
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if not name:
                raise TOMLDecodeError(f"empty table on line {line_number}")
            current = result
            for part in name.split("."):
                key = _key(part, line_number)
                existing = current.setdefault(key, {})
                if not isinstance(existing, dict):
                    raise TOMLDecodeError(f"table conflicts with value on line {line_number}")
                current = existing
            continue
        equal = _outside(line, "=")
        if equal < 0:
            raise TOMLDecodeError(f"expected key/value pair on line {line_number}")
        key = _key(line[:equal], line_number)
        if key in current:
            raise TOMLDecodeError(f"duplicate key on line {line_number}")
        current[key] = _value(line[equal + 1 :], line_number)
    return result


def load(file):
    """Deserialize TOML from an open text or binary file object.

    Args:
        file: A readable file-like object whose ``read()`` method returns
            either a string or UTF-8 encoded bytes.

    Returns:
        A dictionary containing the decoded TOML document.

    Raises:
        UnicodeDecodeError: If binary input is not valid UTF-8.
        TOMLDecodeError: If the document is malformed or unsupported.
        TypeError: If the file returns neither text nor bytes.
    """
    data = file.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return loads(data)


def _format_key(key):
    """Encode an object as a TOML key.

    Args:
        key: The key to encode.  It is converted to ``str`` before encoding.

    Returns:
        A bare key when its characters permit that representation; otherwise,
        a quoted and escaped basic key.
    """
    key = str(key)
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _format_value(value):
    """Encode one Python value using the supported TOML subset.

    Args:
        value: A string, boolean, integer, float, list, or tuple to encode.
            Lists and tuples may recursively contain other supported values.

    Returns:
        A TOML source fragment representing ``value``.

    Raises:
        TypeError: If ``value`` or an item within it has an unsupported type.
    """
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def dumps(data):
    """Serialize a dictionary as TOML text.

    Nested dictionaries are emitted as TOML tables.  Other dictionary values
    must be types accepted by :func:`_format_value`.

    Args:
        data: The dictionary to serialize.  Dictionary insertion order is
            preserved in the generated document.

    Returns:
        A Unicode string containing the TOML document.  Non-empty documents
        end with a newline.

    Raises:
        TypeError: If ``data`` is not a dictionary or contains an unsupported
            value type.
    """
    if not isinstance(data, dict):
        raise TypeError("dumps() expects a dictionary")
    lines = []

    def write_table(table, path):
        """Append one dictionary and its child tables to ``lines``.

        Args:
            table: Dictionary containing the table's values and child tables.
            path: Tuple of table-name components identifying ``table`` in the
                output document.  An empty tuple denotes the document root.

        Returns:
            ``None``.  Serialized lines are appended to the enclosing
            ``lines`` list.
        """
        if path:
            if lines:
                lines.append("")
            lines.append("[" + ".".join(_format_key(part) for part in path) + "]")
        for key, value in table.items():
            if not isinstance(value, dict):
                lines.append(f"{_format_key(key)} = {_format_value(value)}")
        for key, value in table.items():
            if isinstance(value, dict):
                write_table(value, path + (str(key),))

    write_table(data, ())
    return "\n".join(lines) + ("\n" if lines else "")


def dump(data, file):
    """Serialize a dictionary to an open text or binary file object.

    Args:
        data: The dictionary to serialize.  It follows the same restrictions
            as :func:`dumps`.
        file: A writable file-like object.  Text streams receive a string;
            binary streams receive UTF-8 encoded bytes.

    Returns:
        The return value of the file object's ``write()`` method, normally the
        number of characters or bytes written.

    Raises:
        TypeError: If ``data`` contains unsupported values or ``file`` cannot
            accept text or bytes.
    """
    text = dumps(data)
    try:
        return file.write(text)
    except TypeError:
        return file.write(text.encode("utf-8"))
