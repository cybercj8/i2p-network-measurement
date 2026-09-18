import geoip2.database

CITY_DB = "GeoLite2-City.mmdb"

reader = geoip2.database.Reader(CITY_DB)

def lookup_geo(ip):
    try:
        response = reader.city(ip)
        return {
            "country": response.country.iso_code,
            "latitude": response.location.latitude,
            "longitude": response.location.longitude
        }
    except:
        return {"country": None, "latitude": None, "longitude": None}

