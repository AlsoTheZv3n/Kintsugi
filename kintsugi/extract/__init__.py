"""Extraktion: Protokoll, Prioritaetskette, Extraktoren, derived_from.

Importiert die Extraktor-Module fuer den Registrierungs-Seiteneffekt, damit die
Registry sie kennt, sobald irgendetwas aus ``kintsugi.extract`` genutzt wird.
"""

from kintsugi.extract import css as css
from kintsugi.extract import embedded_json as embedded_json
from kintsugi.extract import jsonld as jsonld
