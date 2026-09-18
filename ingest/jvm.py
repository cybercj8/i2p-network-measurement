import jpype
import os

from config import ROUTER_JAR, JAVA_SRC_DIR

def start_jvm():
    global jpype  # <-- FIX: ensure jpype refers to the module

    if jpype.isJVMStarted():
        return

    java_classes = JAVA_SRC_DIR
    router_jar = ROUTER_JAR

    # Start JVM with correct classpath
    jpype.startJVM(
        classpath=[
            router_jar,
            java_classes
        ]
    )

    # Install import hooks AFTER JVM starts
    import jpype.imports

