from pathlib import Path
import unicodedata


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_FILE = ROOT_DIR / "data" / "geonames" / "US.txt"

DOWNLOAD_COMMAND = """mkdir -p data/geonames
curl -L "https://download.geonames.org/export/zip/US.zip" -o data/geonames/US.zip
unzip -o data/geonames/US.zip -d data/geonames"""

STATE_NAME_TO_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "washington dc": "DC",
    "dc": "DC",
}

STATE_ABBRS = set(STATE_NAME_TO_ABBR.values())
_ZIP_INDEX: dict[str, set[str]] | None = None


def normalize_city(city: str) -> str:
    text = unicodedata.normalize("NFKD", city.strip())
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return " ".join(text.casefold().split())


def normalize_state(state: str) -> str | None:
    text = normalize_city(state)
    if len(text) == 2:
        abbr = text.upper()
        return abbr if abbr in STATE_ABBRS else None
    return STATE_NAME_TO_ABBR.get(text)


def _load_zip_index(data_file: Path) -> dict[str, set[str]]:
    if not data_file.exists():
        raise FileNotFoundError(
            f"GeoNames US.txt not found. Download it with:\n{DOWNLOAD_COMMAND}"
        )

    index: dict[str, set[str]] = {}
    with data_file.open("r", encoding="utf-8") as file:
        for line in file:
            columns = line.rstrip("\n").split("\t")
            postal_code = columns[1].strip()
            place_name = columns[2]
            state_abbr = columns[4].strip().upper()
            key = f"{normalize_city(place_name)}|{state_abbr}"
            index.setdefault(key, set()).add(postal_code)
    return index


def get_or_load_zip_index() -> dict[str, set[str]]:
    global _ZIP_INDEX

    if _ZIP_INDEX is None:
        _ZIP_INDEX = _load_zip_index(DEFAULT_DATA_FILE)
    return _ZIP_INDEX


def get_zip_codes(city: str, state: str) -> list[str]:
    """Return GeoNames postal-place ZIPs, not exact legal city-boundary ZIPs."""
    normalized_city = normalize_city(city)
    state_abbr = normalize_state(state)
    if not normalized_city or not state_abbr:
        return []

    index = get_or_load_zip_index()
    return sorted(index.get(f"{normalized_city}|{state_abbr}", set()))


_ALL_ZIPS: frozenset[str] | None = None


def is_known_zip(zip_code: str) -> bool:
    """Return True iff `zip_code` appears anywhere in GeoNames US.txt.
    Used by the UI to reject 5-digit codes that aren't real US ZIPs before
    spawning a runner subprocess."""
    global _ALL_ZIPS
    if _ALL_ZIPS is None:
        _ALL_ZIPS = frozenset().union(*get_or_load_zip_index().values())
    return zip_code in _ALL_ZIPS
