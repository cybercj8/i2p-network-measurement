"""
Machine-specific paths for the collection pipeline.

Every value can be overridden with an environment variable, so the code runs
unchanged on someone else's machine. Defaults match a Homebrew install of
Java I2P and i2pd on macOS.

The dashboard and the analysis code do not need any of these. They are only
used by the collection stages, which require a live local I2P router.
"""
import os

# Java I2P router's netDb directory (the routerInfo records it has gossiped)
JAVA_NETDB_PATH = os.environ.get(
    "I2P_JAVA_NETDB",
    os.path.expanduser("~/Library/Application Support/i2p/netDb"),
)

# Each vantage point's own self-published routerInfo, used to label the two
# routers whose implementation is known with certainty. See ARCHITECTURE.md.
JAVA_ROUTER_INFO = os.environ.get(
    "I2P_JAVA_ROUTER_INFO",
    os.path.expanduser("~/Library/Application Support/i2p/router.info"),
)
I2PD_ROUTER_INFO = os.environ.get(
    "I2PD_ROUTER_INFO",
    "/opt/homebrew/var/lib/i2pd/router.info",
)

# Java I2P's router.jar, loaded through JPype so the pipeline can reuse the
# router's own parsing classes instead of re-implementing the binary format.
ROUTER_JAR = os.environ.get(
    "I2P_ROUTER_JAR",
    "/opt/homebrew/Cellar/i2p/2.12.0/libexec/lib/router.jar",
)

# Directory holding the project's own compiled Java helper classes.
JAVA_SRC_DIR = os.environ.get(
    "I2P_JAVA_SRC",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "java_src"),
)
