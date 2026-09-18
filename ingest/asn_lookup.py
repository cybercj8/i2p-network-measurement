import geoip2.database

ASN_DB = "GeoLite2-ASN.mmdb"

reader = geoip2.database.Reader(ASN_DB)

def lookup_asn(ip):
    try:
        response = reader.asn(ip)
        return {
            "asn": response.autonomous_system_number,
            "asn_name": response.autonomous_system_organization
        }
    except:
        return {"asn": None, "asn_name": None}

