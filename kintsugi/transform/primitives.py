"""Die Transform-Primitiven: strip, nfc, parse_currency, int_from_text.

Alle registrieren sich in der Registry aus ``registry.py``. Nur Standard-
bibliothek (``html``, ``unicodedata``, ``decimal``, ``re``). Kein ``float`` —
Geldwerte wuerden sonst unreproduzierbar (ADR-009 Kontrakt 1).
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from kintsugi.transform.registry import register

# Nicht umbrechende / schmale Leerzeichen, die zu U+0020 werden:
# NBSP, schmales NBSP, Figure Space, Thin Space, Word Joiner.
_NBSP = (chr(0x00A0), chr(0x202F), chr(0x2007), chr(0x2009), chr(0x2060))

# Symbol bzw. ISO-Code -> ISO-4217. Der Wertebereich ist exakt der Pack-Enum
# [GBP, CHF, EUR, USD] (docs/02 §Beispiel). Symbole zuerst, dann Codes.
SYMBOL_TO_ISO: dict[str, str] = {
    "£": "GBP",
    "€": "EUR",
    "$": "USD",
    "GBP": "GBP",
    "EUR": "EUR",
    "USD": "USD",
    "CHF": "CHF",
}


@dataclass(frozen=True)
class Money:
    """Betrag und Waehrung. ``amount`` ist immer ``Decimal``, nie ``float``."""

    amount: Decimal
    currency: str


@register(
    "strip",
    in_type=str | None,
    out_type=str | None,
    failure_mode="Nie ein Fehler; leeres Ergebnis wird zu None statt ''.",
)
def strip(value: str | None) -> str | None:
    """Entschaerft HTML-Entities, normalisiert (NFKC) und kollabiert Whitespace.

    Ein leeres Ergebnis wird ``None``, nicht ``""``: ein Leerstring erfuellte
    still ``required: true`` und blaehte die Fill-Rate auf, die docs/02 „der
    eigentliche Wachhund" nennt.
    """
    if value is None:
        return None
    text = html.unescape(value)
    text = unicodedata.normalize("NFKC", text)
    for nbsp in _NBSP:
        text = text.replace(nbsp, " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


@register(
    "nfc",
    in_type=str | None,
    out_type=str | None,
    failure_mode="Nie ein Fehler; None geht unveraendert durch.",
)
def nfc(value: str | None) -> str | None:
    """Kanonische Unicode-Form (NFC) fuer jeden Payload-Wert vor dem payload_hash.

    Ohne sie erzeugt eine Quelle, die zwischen zerlegter (``e`` + U+0301) und
    zusammengesetzter (``é``) Form wechselt, fuer identische Daten verschiedene
    Hashes, schreibt eine unechte SCD-2-Zeile und bricht die Phase-0-DoD-Zusage
    „zweiter Lauf schreibt keine Duplikate". NFKC-Ausgabe ist bereits
    NFC-komponiert, also ist ``nfc`` nach ``strip`` ein No-op.
    """
    if value is None:
        return None
    return unicodedata.normalize("NFC", value)


@register(
    "parse_currency",
    in_type=str,
    out_type=Money,
    failure_mode="Ohne erkennbares Waehrungssymbol -> None (kein Fehler).",
)
def parse_currency(value: str | None) -> Money | None:
    """Parst ``£51.77`` in ``Money(Decimal('51.77'), 'GBP')``.

    Konstruiert nie ein ``float``. Ohne erkennbares Symbol -> ``None``, damit die
    Fill-Rate-Wache es sieht statt eines Stacktraces. currency wird nicht als
    eigenes Feld extrahiert, sondern via ``derived_from`` aus dem ``Money`` hier
    abgeleitet (ADR-013, F3).
    """
    if value is None:
        return None
    text = value.strip()

    currency: str | None = None
    for token, iso in SYMBOL_TO_ISO.items():
        if token in text:
            currency = iso
            break
    if currency is None:
        return None

    num = text
    for token in SYMBOL_TO_ISO:
        num = num.replace(token, "")
    for nbsp in _NBSP:
        num = num.replace(nbsp, "")
    # Apostroph ist der Schweizer Tausendertrenner.
    num = num.replace(" ", "").replace("'", "")

    num = _normalise_decimal_separators(num)
    try:
        amount = Decimal(num)
    except (InvalidOperation, ValueError):
        return None
    return Money(amount=amount, currency=currency)


def _normalise_decimal_separators(num: str) -> str:
    """Vereinheitlicht Punkt/Komma zu einem Decimal-tauglichen String.

    Kommen beide vor, ist das rechtere das Dezimaltrennzeichen. Kommt nur eines
    vor und folgen ihm genau drei Ziffern, ist es ein Tausendertrenner.
    """
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        if num.rfind(".") > num.rfind(","):
            return num.replace(",", "")
        return num.replace(".", "").replace(",", ".")
    if has_comma:
        after = num.rsplit(",", 1)[1]
        thousands = len(after) == 3 and num.count(",") == 1
        return num.replace(",", "") if thousands else num.replace(",", ".")
    if has_dot:
        after = num.rsplit(".", 1)[1]
        if len(after) == 3 and num.count(".") == 1:
            return num.replace(".", "")
    return num


@register(
    "currency_from_symbol",
    in_type=Money,
    out_type=str,
    failure_mode="None -> None; sonst der ISO-Code des Money-Werts.",
)
def currency_from_symbol(value: Money | None) -> str | None:
    """Leitet currency aus dem ``Money`` von price ab (ADR-013 ``derived_from``).

    Der ``derived_from``-Block von currency nennt price als Quelle und diesen
    Transform. Da price nach ``[strip, parse_currency]`` ein ``Money`` ist,
    liefert dieser Transform dessen Waehrung — ohne die Symbolerkennung zu
    duplizieren.
    """
    return value.currency if value is not None else None


@register(
    "int_from_text",
    in_type=str,
    out_type=int,
    failure_mode="Ohne Zifferngruppe -> None (nie 0, nie ein Fehler).",
)
def int_from_text(value: str | None) -> int | None:
    """Erste Ganzzahl aus dem Text, sonst ``None``.

    ``"Out of stock"`` ergibt ``None``, nicht ``0`` — sonst taeuschte es einen
    echten Lagerbestand von null vor, statt die Fill-Rate von availability zu
    druecken.
    """
    if value is None:
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else None


@register(
    "sha256_slug",
    in_type=str,
    out_type=str,
    failure_mode="None -> None; sonst der hexadezimale sha256 des NFC-Texts.",
)
def sha256_slug(value: str | None) -> str | None:
    """Stabiler Hash-Slug fuer abgeleitete Natural Keys (ADR-013, quotes-Pack).

    Der ``derived_from``-Schritt fuegt die Quellfelder (z. B. author, text)
    zusammen und reicht sie hier herein; das NFC macht den Hash unabhaengig von
    der Unicode-Normalform der Quelle.
    """
    if value is None:
        return None
    normalised = unicodedata.normalize("NFC", value)
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
