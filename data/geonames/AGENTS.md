# GeoNames Postal Data

Postal-code data source: GeoNames, https://www.geonames.org/
GeoNames postal-code data is provided under Creative Commons Attribution.

Download the local U.S. postal-code dataset before using city/state ZIP lookup:

```bash
mkdir -p data/geonames
curl -L "https://download.geonames.org/export/zip/US.zip" -o data/geonames/US.zip
unzip -o data/geonames/US.zip -d data/geonames
```

The expected extracted file is:

```text
data/geonames/US.txt
```

This file is read by `src/zip_lookup.py`.

`US.txt` is tab-delimited UTF-8 text. This project reads postal code, place name, and state abbreviation from the GeoNames rows.

This lookup returns ZIP codes whose GeoNames postal place name matches the given city/state. ZIP codes are postal delivery/address designations, not exact legal city boundaries.

## Snippet:

```text
US	99553	Akutan	Alaska	AK	Aleutians East	013			54.143	-165.7854	1
US	99571	Cold Bay	Alaska	AK	Aleutians East	013			55.1858	-162.7211	1
US	99583	False Pass	Alaska	AK	Aleutians East	013			54.8542	-163.4113	1
US	99612	King Cove	Alaska	AK	Aleutians East	013			55.0628	-162.3056	1
US	99661	Sand Point	Alaska	AK	Aleutians East	013			55.3192	-160.4914	1
```