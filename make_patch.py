#!/usr/bin/env python3
# ESP32-S3 XMC flash bootloop 补丁工具
#
# 作用：把固件 bootloader 里 bootloader_flash_xmc_startup() 函数改成
#       进门直接返回 ESP_OK（等效 espressif/esp-idf#9952 官方白名单补丁），
#       适用于 XMC XM25QH128C（Manufacturer 0x20 / Device 0x7018）flash。
#
# 用法：
#   pip install "capstone>=6.0.0" --pre --upgrade
#   python make_patch.py                       # 默认 ESP32_GENERIC_S3-SPIRAM_OCT-v1.29.0.bin
#   python make_patch.py --fw 其他固件.bin --out 输出.bin
#
# 安全机制：
#   - 打补丁前先在"原固件"上验证 checksum/SHA256 公式，对不上立即中止
#   - 打补丁前断言目标处原字节，地址不对立即中止（换固件版本需重新定位，
#     见 README §5.1 的定位流程）
#
# 仅在 ESP32_GENERIC_S3-SPIRAM_OCT-v1.29.0.bin 上验证通过，风险自负。

import argparse
import hashlib
import struct

# ---- 补丁参数（v1.29.0 SPIRAM_OCT 实测值，换版本必须重新定位）----
TARGET_VADDR = 0x403cbe9b            # bootloader_flash_xmc_startup 的 entry 之后 3 字节处
ORIG_BYTES   = bytes.fromhex('71 1b fe 82')   # 原字节，不符即中止
PATCH_BYTES  = bytes([0x0c, 0x02, 0x1d, 0xf0]) # movi.n a2,0 ; retw.n


def parse_segments(d):
    """ESP32-S3 镜像：偏移 24 起是段表（前有 16 字节扩展头）。返回 [(vaddr, len, file_off)]"""
    nseg = d[1]
    pos = 24
    segs = []
    for _ in range(nseg):
        addr, ln = struct.unpack('<II', bytes(d[pos:pos + 8]))
        segs.append((addr, ln, pos + 8))
        pos += 8 + ln
    return segs, pos


def checksum(d, segs):
    ck = 0xEF
    for _, l, o in segs:
        for b in d[o:o + l]:
            ck ^= b
    return ck & 0xFF


def verify_layout(d, segs, end_of_segs):
    """在原固件上验证校验公式。校验字节位置 = 段结束后填充至其后 16 字节对齐。"""
    ck_off = ((end_of_segs + 1 + 15) & ~15) - 1
    calc = checksum(d, segs)
    assert calc == d[ck_off], 'checksum 公式不匹配（计算 0x%02x，存储 0x%02x），中止!' % (calc, d[ck_off])
    h = hashlib.sha256(bytes(d[:ck_off + 1])).digest()
    stored = bytes(d[ck_off + 1:ck_off + 33])
    assert h == stored, 'sha256 公式不匹配，中止!'
    print('>> checksum @0x%x 与 sha256 公式均验证通过' % ck_off)
    return ck_off


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fw', default='ESP32_GENERIC_S3-SPIRAM_OCT-v1.29.0.bin')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    out = args.out or (args.fw.rsplit('.', 1)[0] + '_XMC_bypass.bin')

    d = bytearray(open(args.fw, 'rb').read())
    segs, end_of_segs = parse_segments(d)
    print('固件 %s：段数 %d，段结束 0x%x' % (args.fw, len(segs), end_of_segs))
    ck_off = verify_layout(d, segs, end_of_segs)

    # ---- 定位目标地址并打补丁 ----
    patched = False
    for a, l, o in segs:
        if a <= TARGET_VADDR < a + l:
            foff = o + (TARGET_VADDR - a)
            old = bytes(d[foff:foff + 4])
            print('补丁位置: vaddr 0x%08x -> 文件偏移 0x%x，原字节 %s' % (TARGET_VADDR, foff, old.hex(' ')))
            assert old == ORIG_BYTES, '原字节与预期不符（换固件版本需重新定位，见 README §5.1），中止!'
            d[foff:foff + 4] = PATCH_BYTES
            print('新字节: %s  (movi.n a2,0 ; retw.n)' % PATCH_BYTES.hex(' '))
            patched = True
    assert patched, '未找到目标地址所在段，中止!'

    # ---- 重算 checksum 与 sha256 ----
    d[ck_off] = checksum(d, segs)
    d[ck_off + 1:ck_off + 33] = hashlib.sha256(bytes(d[:ck_off + 1])).digest()
    open(out, 'wb').write(bytes(d))
    print('\n>> 已保存 %s (%d 字节)' % (out, len(d)))

    # ---- 自检：反汇编确认补丁生效（需 capstone>=6，Xtensa 支持） ----
    try:
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_XTENSA, 0)
        fn = TARGET_VADDR - 3
        for a, l, o in segs:
            if a <= fn < a + l:
                print('=== 补丁后 0x%08x 处的指令 ===' % fn)
                data = bytes(d[o:o + l])
                for ins in md.disasm(data[fn - a:fn - a + 40], fn, count=8):
                    print('  0x%08x  %-12s %s %s' % (
                        ins.address,
                        data[ins.address - a:ins.address - a + ins.size].hex(' '),
                        ins.mnemonic, ins.op_str))
    except ImportError:
        print('(未安装 capstone，跳过反汇编自检)')


if __name__ == '__main__':
    main()
