"""Kanonische Serialisierung, payload_hash und natural_key-Kodierung.

Setzt ADR-009 Kontrakt 1 und 2 um (docs/09-decisions.md). Alle Funktionen hier
sind rein: keine I/O, keine Datenbank, nur die Standardbibliothek. Das ist
Absicht — die Identitaet eines Records (`payload_hash`, `natural_key`,
docs/03-data-model.md Abschnitt Silver) muss byte-genau reproduzierbar sein und
darf weder vom Server noch von der Locale abhaengen.

Der Serialisierer ist bewusst von Hand geschrieben und nutzt nicht
``json.dumps``: die Standardbibliothek kann ``Decimal`` nicht als Zahl-Token
ausgeben, sondern nur ueber ``float`` (verliert Genauigkeit, ``51.77`` wird
unreproduzierbar) oder als String (falscher Typ). Ein eigener Serialisierer
gibt volle Kontrolle ueber jeden dieser Faelle.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast

__all__ = [
    "NaturalKeyMissing",
    "canonical_json",
    "encode_natural_key",
    "payload_hash",
]

# Trennzeichen und Escape fuer den natural_key. \x1f (Unit Separator) ist ein
# Steuerzeichen, das in extrahierten Web-Werten praktisch nie vorkommt.
_NK_SEPARATOR = "\x1f"
_NK_ESCAPE = "\\"


class NaturalKeyMissing(Exception):
    """Eine Komponente des natural_key fehlt, ist None oder leer.

    docs/02-site-packs.md Abschnitt Feldsemantik ist ausdruecklich: ein kaputter
    natural_key korrumpiert den Bestand rueckwirkend. Die Zeile wird deshalb
    verworfen und gezaehlt, nie mit einem Platzhalter erfunden.
    """


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _decimal_token(value: Decimal) -> str:
    """``Decimal`` als schlichtes Zahl-Token, ohne Exponentenschreibweise.

    ``str(Decimal("1E+2"))`` liefert ``"1E+2"``; ``format(..normalize(), "f")``
    liefert ``"100"``. Fixed-Point nach ``normalize`` macht die Darstellung
    eindeutig, sodass zwei gleiche Werte in verschiedener Schreibweise
    (``51.77`` und ``51.770``) denselben Hash ergeben.
    """
    if not value.is_finite():
        raise ValueError(f"nicht-endlicher Decimal ist nicht serialisierbar: {value}")
    return format(value.normalize(), "f")


def _escape_string(text: str) -> str:
    out = ['"']
    for char in text:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _encode(value: object) -> str:
    # bool vor int, weil bool eine Unterklasse von int ist.
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        raise ValueError(
            "None im payload: optionale Felder werden weggelassen, nicht als null "
            "geschrieben (ADR-009 Kontrakt 1). Der Schreibpfad laesst abwesende "
            "Felder aus, statt sie auf None zu setzen."
        )
    if isinstance(value, float):
        raise ValueError(
            f"float {value!r} im payload: Geldwerte und Zahlen mit Nachkommastellen "
            "muessen Decimal sein, sonst ist der Hash nicht reproduzierbar."
        )
    if isinstance(value, Decimal):
        return _decimal_token(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _escape_string(_nfc(value))
    if isinstance(value, Mapping):
        return _encode_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    raise TypeError(f"nicht serialisierbarer Typ im payload: {type(value).__name__}")


def _encode_mapping(mapping: Mapping[object, object]) -> str:
    str_keys: list[str] = []
    for key in mapping:
        if not isinstance(key, str):
            raise TypeError(f"payload-Schluessel muss str sein, nicht {type(key).__name__}")
        str_keys.append(key)
    # Byte-weise nach der NFC-normalisierten UTF-8-Kodierung sortieren, nicht
    # nach Locale. Fuer wohlgeformtes Unicode ist das identisch zur
    # Codepoint-Sortierung; explizit, damit es unabhaengig von der Umgebung ist.
    items = [
        f"{_escape_string(_nfc(key))}:{_encode(mapping[key])}"
        for key in sorted(str_keys, key=lambda k: _nfc(k).encode("utf-8"))
    ]
    return "{" + ",".join(items) + "}"


def canonical_json(payload: Mapping[str, object]) -> bytes:
    """Kanonische UTF-8-Bytes eines payload.

    Sortierte Schluessel, keine unbedeutenden Leerzeichen, NFC-normalisierte
    Strings (Schluessel eingeschlossen), ``Decimal`` als Zahl-Token, abwesende
    Felder weggelassen. ``None`` und ``float`` werden abgelehnt.
    """
    # ``Mapping`` ist im Schluesseltyp invariant, deshalb ist ``Mapping[str, …]``
    # statisch kein ``Mapping[object, …]``. Zur Laufzeit ist die Uebergabe
    # unbedenklich; ``_encode_mapping`` braucht die ``object``-Schluessel, damit
    # sein Nicht-str-Check nicht als toter Code gilt.
    return _encode_mapping(cast("Mapping[object, object]", payload)).encode("utf-8")


def payload_hash(payload: Mapping[str, object]) -> bytes:
    """sha256 ueber ``canonical_json`` — die 32 Rohbytes fuer die bytea-Spalte.

    Gehasht wird in Python, nie in Postgres: JSONB ordnet Schluessel um und
    rendert sie neu, was fuer denselben logischen payload einen anderen Digest
    ergaebe und den Dedup-Raum stillschweigend spalten wuerde.
    """
    return hashlib.sha256(canonical_json(payload)).digest()


def encode_natural_key(components: Sequence[str], values: Mapping[str, object]) -> str:
    """Kodiert die natural_key-Komponenten in genau einen text-Wert.

    Die Reihenfolge stammt aus dem Site-Pack und ist Teil der Identitaet — nie
    sortieren. Trennzeichen und Escape werden escapt, sodass zwei verschiedene
    Komponenten-Tupel nie auf denselben String abbilden. Ein einzelner
    Bestandteil wird als blanker Wert kodiert, damit die Gold-View
    ``gold_book`` ihn unveraendert als ``upc`` ausgeben kann (docs/03 §Gold).

    Fehlt eine Komponente oder ist sie leer, wird ``NaturalKeyMissing``
    geworfen, nie ein Platzhalter zurueckgegeben.
    """
    encoded: list[str] = []
    for name in components:
        if name not in values:
            raise NaturalKeyMissing(f"Komponente {name!r} fehlt im Datensatz")
        raw = values[name]
        if raw is None:
            raise NaturalKeyMissing(f"Komponente {name!r} ist None")
        text = _nfc(str(raw)).strip()
        if not text:
            raise NaturalKeyMissing(f"Komponente {name!r} ist nach dem Strippen leer")
        encoded.append(
            text.replace(_NK_ESCAPE, _NK_ESCAPE * 2).replace(
                _NK_SEPARATOR, _NK_ESCAPE + _NK_SEPARATOR
            )
        )
    return _NK_SEPARATOR.join(encoded)
