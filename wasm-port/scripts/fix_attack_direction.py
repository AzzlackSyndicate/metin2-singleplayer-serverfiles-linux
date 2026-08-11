#!/usr/bin/env python3
"""Fold the camera rotation back into the keyboard attack direction.

`NEW_GetMultiKeyDirRotation` yields a SCREEN-relative rotation (up = 0, left =
+90, down = +180, right = +270), but the character turns in WORLD space, so every
caller has to add the current camera rotation. `NEW_MoveToDirection` still does;
the attack path lost the line when the old

    CCamera* pkCmrCur = CCameraManager::GetCurrentCamera();
    if (pkCmrCur) { float fCmrCurRot = ...; fDirRot = fmod(...); }

wrapper was flattened onto `EngineLib::Engine::GetCamera()` -- only the first
statement survived, leaving `fCmrCurRot` computed and unused. That is why it
compiled with nothing worse than an unused-variable warning, why running was
always correct, and why only striking was off. The offset is not a fixed 45
degrees: it is exactly the camera rotation, so it moves as the camera turns.

Idempotent.

NOTE: this file was regenerated from the applied diff after the original was
deleted by a prune whose keep-pattern read `fix_*.py` and therefore missed the
hyphen in `fix-attack-direction.py`. Hence the underscore in this name.
"""
import io

P = "/opt/m2wasm/src/PyLib/src/bindings/player/PythonPlayerInput.cpp"

OLD = """\t\t\tfloat fCmrCurRot=CameraRotationToCharacterRotation(EngineLib::Engine::Instance().GetCamera().GetRoll());
"""

NEW = """\t\t// NEW_GetMultiKeyDirRotation yields a SCREEN-relative rotation (up = 0,
\t\t// left = +90, down = +180, right = +270), but the character is rotated in
\t\t// WORLD space, so the current camera rotation has to be folded in -- exactly
\t\t// as NEW_MoveToDirection() does above, which is why WASD movement was already
\t\t// correct while the attack landed off by the camera angle. This line was lost
\t\t// when the CCameraManager::GetCurrentCamera() + null-check wrapper was
\t\t// flattened onto EngineLib::Engine::GetCamera(); only the fCmrCurRot
\t\t// computation survived and was then left unused.
\t\tfloat fCmrCurRot = CameraRotationToCharacterRotation(EngineLib::Engine::Instance().GetCamera().GetRoll());
\t\tfDirRot = fmod(360.0f + fCmrCurRot + fDirRot, 360.0f);
"""

s = io.open(P, encoding="utf-8", newline="").read()

# Marker is the whole added statement, not a bare identifier: `fCmrCurRot` alone
# also appears in the untouched line this replaces, and a bare-identifier guard
# has silently skipped an edit three times in this project.
if "fDirRot = fmod(360.0f + fCmrCurRot + fDirRot, 360.0f);" in s:
    print("  already patched: PythonPlayerInput.cpp")
else:
    assert s.count(OLD) == 1, "attack-path camera line not found exactly once"
    io.open(P, "w", encoding="utf-8", newline="").write(s.replace(OLD, NEW, 1))
    print("  patched: PythonPlayerInput.cpp -- camera rotation folded into the attack direction")
