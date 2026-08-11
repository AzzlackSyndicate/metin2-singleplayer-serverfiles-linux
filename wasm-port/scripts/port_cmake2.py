#!/usr/bin/env python3
"""Link Crypto++ into NetworkLib and define the feature macro.

Checked on "cryptopp", which appears nowhere else in this file. Two earlier
checks in this script's predecessor matched their own edit and a Windows-only
block respectively, and each cost a run.
"""
import io
CM = "/opt/m2wasm/src/NetworkLib/CMakeLists.txt"
s = io.open(CM, encoding="utf-8", newline="").read()

if "cryptopp" in s:
    print("  schon vorhanden")
else:
    anchor = "target_link_libraries(${PROJECT_NAME} PUBLIC EterBase)\n"
    assert s.count(anchor) == 1, "Anker nicht eindeutig"
    add = """
# ── _IMPROVED_PACKET_ENCRYPTION_ ────────────────────────────────────────────────
#
# A stock r40250 opens the auth phase with a Diffie-Hellman exchange
# (HEADER_GC_KEY_AGREEMENT, 0xfb) and will not proceed without the client's half.
# This tree had the feature removed, so such a server never answered it; the code
# is ported back from the stock 2014 client, which is the counterpart that server
# was built against.
#
# PUBLIC, not PRIVATE: the guarded member and the packet structs are visible in
# NetStream.h, so every translation unit including it must agree about them --
# otherwise the class has two layouts and the link is quietly wrong.
target_compile_definitions(${PROJECT_NAME} PUBLIC _IMPROVED_PACKET_ENCRYPTION_)

find_package(cryptopp CONFIG QUIET)
if(TARGET cryptopp::cryptopp)
    target_link_libraries(${PROJECT_NAME} PUBLIC cryptopp::cryptopp)
else()
    # Distribution package (libcrypto++-dev): no CMake config, just a library.
    find_library(CRYPTOPP_LIBRARY NAMES cryptopp crypto++ REQUIRED)
    target_link_libraries(${PROJECT_NAME} PUBLIC ${CRYPTOPP_LIBRARY})
endif()
"""
    io.open(CM, "w", encoding="utf-8", newline="").write(s.replace(anchor, anchor + add, 1))
    print("  Crypto++ verlinkt, Schalter definiert")

t = io.open(CM, encoding="utf-8").read()
print("  cipher.cpp in den Quellen : %s" % ("ok" if '"src/cipher.cpp"' in t else "FEHLT"))
print("  Schalter definiert        : %s" % ("ok" if "PUBLIC _IMPROVED_PACKET_ENCRYPTION_" in t else "FEHLT"))
print("  Crypto++ verlinkt         : %s" % ("ok" if "cryptopp" in t else "FEHLT"))
