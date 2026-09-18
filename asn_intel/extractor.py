import json
import ipaddress
import duckdb
import geoip2.database
from pathlib import Path

ASN_DB_PATH = Path("GeoLite2-ASN.mmdb")
CITY_DB_PATH = Path("GeoLite2-City.mmdb")
DUCKDB_PATH = "asn_intel.duckdb"


class ASNExtractor:
    """
    Extracts ASN information from router addresses and writes results
    into router_asn_map. This does NOT overwrite ASN fields already
    present in routerinfo_dataset_enriched.jsonl.
    """

    def __init__(self):
        # Open GeoIP databases
        self.asn_reader = geoip2.database.Reader(str(ASN_DB_PATH))
        self.city_reader = geoip2.database.Reader(str(CITY_DB_PATH))

        # Open DuckDB connection safely
        self.con = duckdb.connect(DUCKDB_PATH)

    # ---------------------------------------------------------
    # Extract ASN + country from an IP
    # ---------------------------------------------------------
    def extract_from_ip(self, ip):
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return None, None, None

        try:
            asn_resp = self.asn_reader.asn(ip)
            city_resp = self.city_reader.city(ip)

            asn = asn_resp.autonomous_system_number
            asn_name = asn_resp.autonomous_system_organization
            country = city_resp.country.iso_code

            return asn, asn_name, country
        except Exception:
            return None, None, None

    # ---------------------------------------------------------
    # Extract ASN from router options
    # ---------------------------------------------------------
    def extract_from_options(self, options_str):
        if not isinstance(options_str, str):
            return None, None, None

        parts = options_str.replace("{", "").replace("}", "").split(",")
        kv = {}

        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip()] = v.strip()

        host = kv.get("host")
        if host:
            return self.extract_from_ip(host)

        return None, None, None

    # ---------------------------------------------------------
    # Extract ASN from router addresses
    # ---------------------------------------------------------
    def extract_from_addresses(self, addresses):
        results = []

        for addr in addresses:
            host = addr.get("host")
            if host:
                asn, asn_name, country = self.extract_from_ip(host)
                if asn:
                    results.append(("host", asn, asn_name, country))

            options = addr.get("options")
            if options:
                asn, asn_name, country = self.extract_from_options(options)
                if asn:
                    results.append(("options", asn, asn_name, country))

        return results

    # ---------------------------------------------------------
    # Pick the best ASN from multiple sources
    # ---------------------------------------------------------
    def resolve_best_asn(self, extracted):
        if not extracted:
            return None, None, None, None

        counts = {}
        for source, asn, asn_name, country in extracted:
            counts.setdefault(asn, []).append((source, asn_name, country))

        best_asn = max(counts, key=lambda k: len(counts[k]))
        entries = counts[best_asn]

        confidence = len(entries) / len(extracted)
        source = entries[0][0]
        asn_name = entries[0][1]
        country = entries[0][2]

        return best_asn, asn_name, country, confidence

    # ---------------------------------------------------------
    # Write ASN mapping to DuckDB
    # ---------------------------------------------------------
    def update_router_asn(self, router_hash, asn, confidence, source):
        self.con.execute("""
            INSERT INTO router_asn_map (router_hash, asn, asn_confidence, extracted_from)
            VALUES (?, ?, ?, ?)
        """, [router_hash, asn, confidence, source])

    # ---------------------------------------------------------
    # Process a single router entry
    # ---------------------------------------------------------
    def process_router(self, router):
        # DO NOT overwrite ASN already present in enriched dataset
        if router.get("asn") is not None:
            return router["asn"], router.get("asn_name"), router.get("country"), 1.0

        addresses = router.get("addresses", [])
        extracted = self.extract_from_addresses(addresses)
        asn, asn_name, country, confidence = self.resolve_best_asn(extracted)

        if asn:
            self.update_router_asn(router["router_hash"], asn, confidence, "multi-source")

        return asn, asn_name, country, confidence

    # ---------------------------------------------------------
    # MAIN EXTRACTION LOOP
    # ---------------------------------------------------------
    def run(self):
        with open("routerinfo_dataset_enriched.jsonl") as f:
            for line in f:
                router = json.loads(line)
                self.process_router(router)

    # ---------------------------------------------------------
    # Close all resources safely
    # ---------------------------------------------------------
    def close(self):
        try:
            self.asn_reader.close()
        except:
            pass

        try:
            self.city_reader.close()
        except:
            pass

        try:
            self.con.close()
        except:
            pass

