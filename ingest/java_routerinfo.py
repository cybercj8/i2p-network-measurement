import jpype
import jpype.imports
import os

# Cache for Java parse() method
java_parse = None


def start_jvm():
    """Start JVM once with correct classpath."""
    if jpype.isJVMStarted():
        return

    JAVA_CLASSES = os.path.abspath("java_src")
    XZ_JAR = os.path.abspath("java_libs/xz.jar")
    I2P_LIBS = "/opt/homebrew/Cellar/i2p/2.12.0/libexec/lib/*"

    classpath = f"{JAVA_CLASSES}:{XZ_JAR}:{I2P_LIBS}"

    jpype.startJVM(
        jpype.getDefaultJVMPath(),
        "-ea",
        f"-Djava.class.path={classpath}"
    )


def _to_python(value):
    """
    Convert ANY Java object into a pure Python type.
    Ensures DuckDB will always accept the result.
    """
    if value is None:
        return None

    # Java strings → Python str
    if isinstance(value, jpype.JString):
        return str(value)

    # Java numbers → Python int/float
    # Same boxed-type gap as the boolean case below: a primitive long/int/
    # short/float/double autoboxed into a Map<String,Object> comes back as
    # java.lang.Long/Integer/Short/Float/Double *objects*, which don't
    # match isinstance(value, jpype.JLong) etc (that only matches the
    # primitive wrapper types) -- found via RouterInfo's "published" field
    # silently coming through as the string '1783468046331' instead of an
    # int. hasattr(value, "longValue")/"doubleValue" can't disambiguate
    # which boxed type it is, since every java.lang.Number subclass
    # implements both accessors -- check the actual class name instead.
    if isinstance(value, (jpype.JInt, jpype.JLong, jpype.JShort)):
        return int(value)
    if isinstance(value, (jpype.JFloat, jpype.JDouble)):
        return float(value)
    if hasattr(value, "getClass"):
        class_name = str(value.getClass().getSimpleName())
        if class_name in ("Integer", "Long", "Short", "Byte"):
            return int(value)
        if class_name in ("Float", "Double"):
            return float(value)

    # Java booleans → Python bool
    # NOTE: a primitive `boolean` autoboxed into a Map<String,Object> (as
    # RouterInfoDump.java does via out.put("supports_ipv6", false)) comes
    # back through JPype as a java.lang.Boolean *object*, which does NOT
    # match isinstance(value, jpype.JBoolean) -- that only matches the
    # primitive wrapper type. Without this check it fell through to the
    # final "convert to string" branch below, turning `false` into the
    # non-empty Python string "False" -- which is truthy, silently making
    # every boolean field True regardless of its real value.
    if isinstance(value, jpype.JBoolean) or hasattr(value, "booleanValue"):
        return bool(value)

    # Java arrays → Python lists
    if isinstance(value, jpype.JArray):
        return [_to_python(v) for v in value]

    # Java Maps → Python dict
    if hasattr(value, "entrySet"):
        return {str(e.getKey()): _to_python(e.getValue()) for e in value.entrySet()}

    # Fallback: convert to string
    return str(value)


def parse_routerinfo_java(raw_bytes):
    """
    Calls the Java parser, then converts ALL returned values
    into pure Python types that DuckDB can store.

    Handles BOTH:
    - Normal Java Map output
    - Raw fallback Java String output (Option C)
    """
    global java_parse

    start_jvm()

    if java_parse is None:
        from i2p.parser.RouterInfoDump import parse as _java_parse
        java_parse = _java_parse

    try:
        result = java_parse(raw_bytes)

        # ---------------------------------------------------------
        # CASE 1: Java returned a raw fallback string
        # ---------------------------------------------------------
        if isinstance(result, jpype.JString):
            raw_str = str(result)

            return {
                "router_hash": None,
                "published": None,
                "caps": None,
                "version": None,
                "ip": None,
                "supports_ipv6": None,
                "transport": None,
                "raw_routerinfo": raw_str,   # <-- Option C: preserve raw data
            }

        # ---------------------------------------------------------
        # CASE 2: Normal Java Map output
        # ---------------------------------------------------------
        py = {}
        for key in result.keySet():
            py[str(key)] = _to_python(result.get(key))

        return {
            "router_hash": py.get("router_hash"),
            "published": py.get("published"),
            "caps": py.get("caps"),
            "version": py.get("version"),
            "ip": py.get("ip"),
            "supports_ipv6": py.get("supports_ipv6"),
            "transport": py.get("transport"),
            "raw_routerinfo": None,        # no raw fallback needed
        }

    except Exception as e:
        print(f"[JAVA ERROR] {e}")
        return None

