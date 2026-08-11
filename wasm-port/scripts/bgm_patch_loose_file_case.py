#!/usr/bin/env python3
"""
Make loose (on-disk) asset lookups case-insensitive on a case-sensitive filesystem.

WHY: CEterPackManager::GetFromPack folds the requested name (ConvertFileName ->
stl_lowers) but GetFromFile passed it to std::ifstream verbatim. On Linux that makes
every loose asset whose on-disk spelling differs in case from the spelling in the
scripts unreachable. The reported symptom is the field music: the scripts ask for
"BGM/<track>.mp3", the pack layer folds it to "bgm/<track>.mp3", and only the five
tracks that also live in pack/bgm.zip ever resolved.

Idempotent: each edit is guarded by a marker string that occurs exactly once in the
patched file, and the guard asserts an exact occurrence count rather than truthiness.
"""

import sys

HDR = "/opt/m2wasm/src/EterPack/EterPackManager.h"
SRC = "/opt/m2wasm/src/EterPack/EterPackManager.cpp"
SND = "/opt/m2wasm/src/EngineLib/src/bgfx/audio/BgfxSound.cpp"

# Markers are long and unique enough that they cannot collide with prose elsewhere.
M_HDR = "static std::string ResolveLooseFileName(const std::string& fileName);"
M_INC = "#include <filesystem>"
M_IMPL = "std::string CEterPackManager::ResolveLooseFileName(const std::string& fileName)"
M_RETRY = "// THE CASE-FOLDED RETRY -- ResolveLooseFileName above carries the rationale."
M_EXIST = "bool CEterPackManager::isExistAsFile(const std::string& fileName)"
M_SND = "not found in any pack or on disk"


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def apply(text, marker, anchor, replacement, what):
    """Replace `anchor` with `replacement` unless `marker` is already present."""
    n = text.count(marker)
    if n == 1:
        print("  skip (already patched): %s" % what)
        return text, False
    if n > 1:
        raise SystemExit("ABORT: marker for '%s' occurs %d times; refusing." % (what, n))
    a = text.count(anchor)
    if a != 1:
        raise SystemExit("ABORT: anchor for '%s' occurs %d times (expected 1)." % (what, a))
    print("  patch: %s" % what)
    return text.replace(anchor, replacement), True


# ---------------------------------------------------------------- header ----------
HDR_ANCHOR = "\t\tstd::string ConvertFileName(std::string fileName);\n"

HDR_NEW = HDR_ANCHOR + """
		// -- LOOSE FILES ON A CASE-SENSITIVE FILESYSTEM ------------------------------
		//
		// Returns the real on-disk spelling of `fileName` when a case-insensitive match
		// exists and DIFFERS from the spelling asked for; returns "" when the caller's
		// own spelling is the only candidate, when nothing matches, and always on
		// Windows (where the filesystem already does this and a directory scan would be
		// pure cost). EterPackManager.cpp carries the full rationale.
		static std::string ResolveLooseFileName(const std::string& fileName);

		// Drops the cached per-directory listings the resolver builds. Nothing in the
		// client calls this; it exists so a test can create files and be seen.
		static void ResetLooseFileNameCache();
"""

# ---------------------------------------------------------------- includes --------
INC_ANCHOR = "#include <exception>\n"
INC_NEW = "#include <exception>\n#include <filesystem>\n#include <unordered_map>\n"

# ---------------------------------------------------------------- resolver --------
IMPL_ANCHOR = "bool CEterPackManager::GetFromFile(const std::string& fileName, TPackDataPtr& dataPtr)\n"

IMPL_NEW = r'''// =====================================================================================
// LOOSE FILES ON A CASE-SENSITIVE FILESYSTEM
//
// >>> GetFromPack FOLDS THE REQUESTED NAME AND GetFromFile DID NOT, SO EVERY LOOSE ASSET
// >>> WHOSE ON-DISK SPELLING DIFFERS IN CASE FROM THE SPELLING IN THE SCRIPTS WAS
// >>> UNREACHABLE ON LINUX. <<<
//
// The symptom that found it was the FIELD MUSIC. root/musicinfo.py and root/game.py ask
// for "BGM/<track>.mp3"; BgfxSound::Acquire folds that to "bgm/<track>.mp3" for its cache
// key and hands the folded name on; the shipped directory is `BGM`. GetFromPack folds
// too, so it answered for the five tracks that also live in pack/bgm.zip -- login_window,
// characterselect, m2bg, desert, wedding -- which is EXACTLY the set that played. The
// other twenty tracks, every one of them a map theme, exist only as loose files under
// BGM/ and resolved to nothing: the login screen had music and the world was silent.
//
// >>> THE FIX BELONGS IN THE CLIENT, NOT IN THE DATA. <<< Renaming BGM/ to bgm/ would fix
// this one directory and leave the next one, and the asset tree is content shared
// byte-for-byte with the Windows build and with every server's patcher. Application.cpp
// already had to make this same call for the pack index itself ("a client that only
// accepts one capitalisation would break on half of them").
//
// COST: NOTHING ON THE HAPPY PATH. The direct open is tried first and returns
// immediately; only a miss walks the path. Per-directory listings are cached, so a miss
// costs one hash lookup per component after the first visit to each directory. That
// matters: in file-first (dev) search mode GetFromFile is tried, and misses, for every
// asset that lives in a pack.
//
// STALENESS: a cached listing does not see files created afterwards. Harmless here,
// because anything the client writes it reads back with the spelling it wrote, and that
// open succeeds before the resolver is ever consulted.
namespace
{
	// folded component name -> the spelling that is actually on disk
	using LooseDirIndex = std::unordered_map<std::string, std::string>;

	// Function-local statics for the same destruction-order reason the census above
	// gives: this class is a CSingleton and a file-scope map would be a teardown fiasco.
	std::map<std::string, LooseDirIndex>& LooseDirCache()
	{
		static std::map<std::string, LooseDirIndex> s_cache;
		return s_cache;
	}

	Mutex& LooseDirMutex()
	{
		static Mutex s_mutex;
		return s_mutex;
	}

	// ASCII a..z ONLY, matching stl_lowers in ConvertFileName. A locale-aware fold would
	// disagree with the pack index for the same bytes, which is worse than not folding.
	std::string FoldAscii(std::string s)
	{
		for (char& c : s)
			if (c >= 'A' && c <= 'Z')
				c = static_cast<char>(c - 'A' + 'a');
		return s;
	}

	// Caller holds LooseDirMutex. A missing or unreadable directory is cached as an EMPTY
	// index rather than left absent, so it is not re-scanned on every subsequent miss --
	// which is the common case for the "d:/ymir work/..." paths the data files carry.
	const LooseDirIndex& LooseDirEntries(const std::string& directory)
	{
		auto it = LooseDirCache().find(directory);
		if (it != LooseDirCache().end())
			return it->second;

		namespace fs = std::filesystem;
		LooseDirIndex index;
		try
		{
			std::error_code ec;
			const fs::path root = directory.empty() ? fs::path(".") : fs::path(directory);
			fs::directory_iterator dit(root, fs::directory_options::skip_permission_denied, ec);
			if (!ec)
			{
				for (const fs::directory_entry& entry : dit)
				{
					const std::string name = entry.path().filename().string();
					// emplace, not insert_or_assign: a case-insensitive filesystem cannot
					// hold two spellings that fold together, so which one wins here is
					// arbitrary either way -- but keeping the FIRST is at least stable
					// for the lifetime of the process.
					index.emplace(FoldAscii(name), name);
				}
			}
		}
		catch (const std::exception&)
		{
			// A directory that vanishes mid-iteration is a miss, not a crash.
			index.clear();
		}

		return LooseDirCache().emplace(directory, std::move(index)).first->second;
	}
}

std::string CEterPackManager::ResolveLooseFileName(const std::string& fileName)
{
#ifdef _WIN32
	(void)fileName;
	return std::string();
#else
	if (fileName.empty())
		return std::string();

	std::string resolved;
	std::size_t pos = 0;

	if (fileName.front() == '/')
	{
		resolved = "/";
		pos = 1;
	}

	// Only a spelling that actually DIFFERS is worth handing back; otherwise the caller
	// would retry an open it already knows fails.
	bool changed = false;

	for (;;)
	{
		const std::size_t slash = fileName.find('/', pos);
		const bool last = (slash == std::string::npos);
		const std::string component =
			fileName.substr(pos, last ? std::string::npos : slash - pos);

		if (component.empty() || component == "." || component == "..")
		{
			// Nothing to match against a listing; keep it verbatim.
			resolved += component;
		}
		else
		{
			std::string real;
			{
				FinderLock lock(LooseDirMutex());
				// `resolved` is "" for the first component of a relative path, which
				// LooseDirEntries reads as the working directory.
				const LooseDirIndex& index = LooseDirEntries(resolved);
				const auto hit = index.find(FoldAscii(component));
				if (hit == index.end())
					return std::string();   // no on-disk spelling matches this component
				real = hit->second;
			}

			if (real != component)
				changed = true;
			resolved += real;
		}

		if (last)
			break;

		resolved += '/';
		pos = slash + 1;
	}

	return changed ? resolved : std::string();
#endif
}

void CEterPackManager::ResetLooseFileNameCache()
{
	FinderLock lock(LooseDirMutex());
	LooseDirCache().clear();
}

'''

IMPL_NEW = IMPL_NEW + IMPL_ANCHOR

# ---------------------------------------------------------------- GetFromFile -----
RETRY_ANCHOR = """	// Try to open the file
	std::ifstream file(fileName, std::ios::binary);
	if (!file.is_open())
		return false;
"""

RETRY_NEW = """	// Try to open the file
	std::ifstream file(fileName, std::ios::binary);

	// THE CASE-FOLDED RETRY -- ResolveLooseFileName above carries the rationale.
	// Reached ONLY when the caller's own spelling did not open, so the common path pays
	// nothing for it.
	if (!file.is_open())
	{
		const std::string real = ResolveLooseFileName(fileName);
		if (real.empty())
			return false;

		file = std::ifstream(real, std::ios::binary);
		if (!file.is_open())
			return false;
	}
"""

# ---------------------------------------------------------------- isExist ---------
EXIST_ANCHOR = """bool CEterPackManager::isExist(const char * c_szFileName)
{
	if (m_iSearchMode == SEARCH_PACK_FIRST)
	{
		if (isExistInPack(c_szFileName))
			return true;

		return _access(c_szFileName, 0) == 0;
	}

	if (_access(c_szFileName, 0) == 0)
		return true;

	return isExistInPack(c_szFileName);
}
"""

EXIST_NEW = """bool CEterPackManager::isExistAsFile(const std::string& fileName)
{
	if (_access(fileName.c_str(), 0) == 0)
		return true;

	// The same folded retry GetFromFile does, for the same reason. Without it
	// app.IsExistFile("BGM/xmas.mp3") answers false for a file the client can now
	// actually read, and game.py gates the christmas theme on exactly that call.
	const std::string real = ResolveLooseFileName(fileName);
	return !real.empty() && _access(real.c_str(), 0) == 0;
}

bool CEterPackManager::isExist(const char * c_szFileName)
{
	if (m_iSearchMode == SEARCH_PACK_FIRST)
	{
		if (isExistInPack(c_szFileName))
			return true;

		return isExistAsFile(c_szFileName);
	}

	if (isExistAsFile(c_szFileName))
		return true;

	return isExistInPack(c_szFileName);
}
"""

EXIST_DECL_ANCHOR = "\t\tbool GetFromFile(const std::string& fileName, TPackDataPtr& dataPtr);\n"
EXIST_DECL_NEW = (
    EXIST_DECL_ANCHOR
    + "\t\tbool isExistAsFile(const std::string& fileName);\n"
)


# ---------------------------------------------------------------- BgfxSound ------
# The miss was SILENT. PlayMusic says "Acquire already logged why, once" -- true for a
# DECODE failure, false for a name that resolved to no bytes at all, which returned
# nullptr without a word. That is why twenty missing map themes looked exactly like
# "this map has no music" for as long as they did.
SND_ANCHOR = """    CEterPackManager::TPackDataPtr data;
    if (!CEterPackManager::Instance().Get(key, data) || !data || data->empty())
    {
        std::lock_guard<std::mutex> lock(m_cacheLock);
        m_cacheFailed.emplace(key, true);
        return nullptr;
    }
"""

SND_NEW = """    CEterPackManager::TPackDataPtr data;
    if (!CEterPackManager::Instance().Get(key, data) || !data || data->empty())
    {
        std::lock_guard<std::mutex> lock(m_cacheLock);
        // ONCE PER PATH, on the .second of the negative-cache insert. It says NOT FOUND,
        // which is a DIFFERENT failure from the decode warning below and needs saying:
        // PlayMusic's "Acquire already logged why" was only ever true for a decode, so a
        // name that resolved to no bytes returned nullptr in silence. Twenty map themes
        // were unreachable for exactly that long because "no music here" and "the client
        // cannot find the file" produced identical logs.
        if (m_cacheFailed.emplace(key, true).second)
            SPDLOG_WARN("BgfxSound: '{}' not found in any pack or on disk - nothing will play", key);
        return nullptr;
    }
"""


def main():
    print("== header ==")
    h = read(HDR)
    h, c1 = apply(h, M_HDR, HDR_ANCHOR, HDR_NEW, "declare ResolveLooseFileName")
    h, c2 = apply(h, "bool isExistAsFile(const std::string& fileName);",
                  EXIST_DECL_ANCHOR, EXIST_DECL_NEW, "declare isExistAsFile")
    if c1 or c2:
        write(HDR, h)

    print("== source ==")
    s = read(SRC)
    s, d1 = apply(s, M_INC, INC_ANCHOR, INC_NEW, "includes")
    s, d2 = apply(s, M_IMPL, IMPL_ANCHOR, IMPL_NEW, "resolver implementation")
    s, d3 = apply(s, M_RETRY, RETRY_ANCHOR, RETRY_NEW, "GetFromFile folded retry")
    s, d4 = apply(s, M_EXIST, EXIST_ANCHOR, EXIST_NEW, "isExist folded retry")
    if d1 or d2 or d3 or d4:
        write(SRC, s)

    print("== BgfxSound ==")
    b = read(SND)
    b, e1 = apply(b, M_SND, SND_ANCHOR, SND_NEW, "log a not-found sound once per path")
    if e1:
        write(SND, b)

    print("OK")


if __name__ == "__main__":
    main()
