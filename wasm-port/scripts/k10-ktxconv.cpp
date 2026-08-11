// k10-ktxconv — offline experiment tool. Reads a .dds, re-encodes every mip to a target
// GPU format, writes a KTX2, and reports size + SSIM against the source.
//
// Links the ALREADY-BUILT host bimg/bx static libs from build/bin/Release. It builds and
// runs entirely outside the client's build system: no CMake target, no relink of anything
// the running desktop client owns.
#include <bimg/bimg.h>
#include <bimg/decode.h>
#include <bimg/encode.h>
#include <bx/allocator.h>
#include <bx/file.h>
#include <bx/error.h>

#include <cstdio>
#include <cstring>
#include <cmath>
#include <string>
#include <vector>

static bx::DefaultAllocator s_allocator;

struct Target { const char* name; bimg::TextureFormat::Enum fmt; };

static const Target kTargets[] = {
    { "astc4x4", bimg::TextureFormat::ASTC4x4 },
    { "astc6x6", bimg::TextureFormat::ASTC6x6 },
    { "astc8x8", bimg::TextureFormat::ASTC8x8 },
    { "etc2",    bimg::TextureFormat::ETC2    },
    { "etc2a",   bimg::TextureFormat::ETC2A   },
    { "bc1",     bimg::TextureFormat::BC1     },
    { "bc3",     bimg::TextureFormat::BC3     },
};

static std::vector<uint8_t> readFile(const char* path)
{
    std::vector<uint8_t> out;
    FILE* f = fopen(path, "rb");
    if (f == nullptr) return out;
    fseek(f, 0, SEEK_END);
    const long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    out.resize(size_t(n));
    if (n > 0 && fread(out.data(), 1, size_t(n), f) != size_t(n)) out.clear();
    fclose(f);
    return out;
}

// Decode one mip of a container into freshly allocated RGBA8.
static bool decodeMipRgba8(const bimg::ImageContainer& img, uint8_t lod, std::vector<uint8_t>& rgba,
                           uint32_t& w, uint32_t& h)
{
    bimg::ImageMip mip;
    if (!bimg::imageGetRawData(img, 0, lod, img.m_data, img.m_size, mip)) return false;
    w = mip.m_width;
    h = mip.m_height;
    rgba.assign(size_t(w) * h * 4, 0);
    bimg::imageDecodeToRgba8(&s_allocator, rgba.data(), mip.m_data, w, h, w * 4, mip.m_format);
    return true;
}

int main(int argc, char** argv)
{
    if (argc < 4)
    {
        fprintf(stderr, "usage: k10-ktxconv <in.dds> <target> <out.ktx2> [quality]\n");
        for (const Target& t : kTargets) fprintf(stderr, "  target: %s\n", t.name);
        return 2;
    }

    const char* inPath  = argv[1];
    const char* tName   = argv[2];
    const char* outPath = argv[3];
    const bimg::Quality::Enum quality =
        (argc > 4 && 0 == strcmp(argv[4], "highest")) ? bimg::Quality::Highest
      : (argc > 4 && 0 == strcmp(argv[4], "fastest")) ? bimg::Quality::Fastest
      : bimg::Quality::Default;

    bimg::TextureFormat::Enum dstFormat = bimg::TextureFormat::Count;
    for (const Target& t : kTargets) if (0 == strcmp(t.name, tName)) dstFormat = t.fmt;
    if (dstFormat == bimg::TextureFormat::Count) { fprintf(stderr, "unknown target %s\n", tName); return 2; }

    std::vector<uint8_t> src = readFile(inPath);
    if (src.empty()) { fprintf(stderr, "cannot read %s\n", inPath); return 1; }

    bx::Error err;
    // The DECODE variant: exactly what BgfxTexture::LoadFromMemory calls.
    bimg::ImageContainer* in = bimg::imageParse(&s_allocator, src.data(), uint32_t(src.size()),
                                                bimg::TextureFormat::Count, &err);
    if (in == nullptr) { fprintf(stderr, "imageParse failed: %s\n", err.getMessage().getCPtr()); return 1; }

    // The file's own declared mip count (headers only) — the number the client's
    // truncated-chain arm uses. KTX1/DDS only; KTX2 is not handled by this overload.
    bimg::ImageContainer info;
    bx::Error infoErr;
    const bool haveInfo = bimg::imageParse(info, src.data(), uint32_t(src.size()), &infoErr) && infoErr.isOk();

    printf("SRC   %s  %ux%u  fmt=%s  mips=%u(decoded) %u(declared)  layers=%u  cube=%d  bytes=%zu\n",
           inPath, in->m_width, in->m_height, bimg::getName(in->m_format),
           in->m_numMips, haveInfo ? info.m_numMips : 0, in->m_numLayers, int(in->m_cubeMap), src.size());

    if (in->m_cubeMap || in->m_numLayers > 1)
    {
        fprintf(stderr, "cubemap/array not handled by this experiment tool\n");
        bimg::imageFree(in);
        return 1;
    }

    // Allocate the destination with the SAME mip count the decoded source carries. Note
    // that imageAlloc takes a BOOL for mips and therefore always builds the full chain —
    // which is what we want here: a truncated chain in a KTX2 would be uploaded with an
    // uninitialised tail by the client (bimg's container-only imageParse cannot read KTX2,
    // so the client's truncated-chain arm never fires for one).
    bimg::ImageContainer* out = bimg::imageAlloc(&s_allocator, dstFormat, uint16_t(in->m_width),
                                                 uint16_t(in->m_height), uint16_t(in->m_depth),
                                                 in->m_numLayers, in->m_cubeMap, in->m_numMips > 1);
    if (out == nullptr) { fprintf(stderr, "imageAlloc failed\n"); bimg::imageFree(in); return 1; }

    // ── THE LEVELS PAST THE FILE'S DECLARED COUNT ARE UNINITIALISED HEAP, NOT PIXELS ──
    //
    // BgfxTexture.cpp:627-636 measured it: 3,179 of the 8,182 .dds in this corpus declare a
    // TRUNCATED chain, the decode overload of imageParse allocates the FULL chain anyway
    // (imageAlloc takes a bool), and it leaves the tail as raw bx::alloc memory. Encoding
    // that tail would BAKE the garbage into the KTX2 permanently — and the client cannot
    // even detect it afterwards, because bimg's container-only imageParse does not know
    // KTX2, so BgfxTexture's truncated-chain arm can never fire for one.
    //
    // So: real levels come from the file, and every level past `declaredMips` is generated
    // here by a box filter over the previous level. The output always carries a full chain.
    const uint8_t declaredMips = haveInfo && info.m_numMips > 0 ? info.m_numMips : in->m_numMips;

    std::vector<uint8_t> prev;   // previous level, RGBA8
    uint32_t pw = 0, ph = 0;

    for (uint8_t lod = 0; lod < out->m_numMips; ++lod)
    {
        std::vector<uint8_t> rgba;
        uint32_t w = 0, h = 0;

        if (lod < declaredMips)
        {
            if (!decodeMipRgba8(*in, lod, rgba, w, h)) break;
        }
        else
        {
            // Box filter from the level above — the levels the file simply does not carry.
            if (prev.empty()) break;
            w = pw > 1 ? pw / 2 : 1;
            h = ph > 1 ? ph / 2 : 1;
            rgba.assign(size_t(w) * h * 4, 0);
            for (uint32_t y = 0; y < h; ++y)
            for (uint32_t x = 0; x < w; ++x)
            for (uint32_t c = 0; c < 4; ++c)
            {
                const uint32_t x0 = bx::min(x * 2, pw - 1), x1 = bx::min(x * 2 + 1, pw - 1);
                const uint32_t y0 = bx::min(y * 2, ph - 1), y1 = bx::min(y * 2 + 1, ph - 1);
                const uint32_t s = uint32_t(prev[(y0 * pw + x0) * 4 + c]) + prev[(y0 * pw + x1) * 4 + c]
                                 + prev[(y1 * pw + x0) * 4 + c] + prev[(y1 * pw + x1) * 4 + c];
                rgba[(y * w + x) * 4 + c] = uint8_t(s / 4);
            }
        }

        prev = rgba; pw = w; ph = h;

        bimg::ImageMip dstMip;
        if (!bimg::imageGetRawData(*out, 0, lod, out->m_data, out->m_size, dstMip)) break;

        bx::Error encErr;
        bimg::imageEncodeFromRgba8(&s_allocator, const_cast<uint8_t*>(dstMip.m_data), rgba.data(),
                                   w, h, 1, dstFormat, quality, &encErr);
        if (!encErr.isOk())
        {
            fprintf(stderr, "encode lod %u failed: %s\n", lod, encErr.getMessage().getCPtr());
            bimg::imageFree(in); bimg::imageFree(out);
            return 1;
        }
    }

    // Write the KTX2.
    bx::FileWriter writer;
    bx::Error wErr;
    if (!writer.open(bx::FilePath(outPath), false, &wErr))
    { fprintf(stderr, "cannot open %s\n", outPath); bimg::imageFree(in); bimg::imageFree(out); return 1; }
    const int32_t written = bimg::imageWriteKtx2(&writer, out->m_format, false, out->m_width, out->m_height,
                                                 out->m_depth, out->m_numMips, out->m_numLayers,
                                                 out->m_srgb, out->m_data, &wErr);
    writer.close();
    if (!wErr.isOk()) { fprintf(stderr, "imageWriteKtx2: %s\n", wErr.getMessage().getCPtr()); return 1; }

    // THE ACID TEST: re-parse the file we just wrote through the same call the client makes.
    std::vector<uint8_t> back = readFile(outPath);
    bx::Error backErr;
    bimg::ImageContainer* rt = bimg::imageParse(&s_allocator, back.data(), uint32_t(back.size()),
                                                bimg::TextureFormat::Count, &backErr);
    printf("OUT   %s  target=%s  bytes=%d  reparse=%s  fmt=%s  mips=%u\n",
           outPath, tName, written, rt ? "OK" : "FAILED",
           rt ? bimg::getName(rt->m_format) : "-", rt ? rt->m_numMips : 0);
    if (rt == nullptr) fprintf(stderr, "  reparse error: %s\n", backErr.getMessage().getCPtr());

    // Quality: mip 0, source-as-decoded vs round-tripped.
    if (rt != nullptr)
    {
        std::vector<uint8_t> a, b;
        uint32_t aw = 0, ah = 0, bw = 0, bh = 0;
        if (decodeMipRgba8(*in, 0, a, aw, ah) && decodeMipRgba8(*rt, 0, b, bw, bh)
            && aw == bw && ah == bh)
        {
            const float ssim = bimg::imageQualityRgba8(a.data(), b.data(), uint16_t(aw), uint16_t(ah));
            // Plain RMSE/PSNR over RGBA, computed here because SSIM alone hides alpha damage.
            double se = 0.0;
            for (size_t i = 0; i < a.size(); ++i) { const double d = double(a[i]) - double(b[i]); se += d * d; }
            const double mse  = se / double(a.size());
            const double psnr = mse > 0.0 ? 10.0 * log10(255.0 * 255.0 / mse) : 99.0;
            printf("QUAL  ssim=%.4f  psnr=%.2f dB  (%ux%u, alpha=%d)\n", ssim, psnr, aw, ah, int(in->m_hasAlpha));
        }
        bimg::imageFree(rt);
    }

    bimg::imageFree(in);
    bimg::imageFree(out);
    return 0;
}
