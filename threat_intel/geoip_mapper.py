import geoip2.database

reader = geoip2.database.Reader("GeoLite2-Country.mmdb")

def ip_to_country(ip):
    try:
        rec = reader.country(ip)
        return rec.country.iso_code
    except:
        return None

