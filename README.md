# 不换芯片救活 ESP32-S3：给 XMC flash 打 4 字节二进制补丁

> A 4-byte binary patch that revives ESP32-S3 boards stuck in `XMC flash startup fail` bootloop — no recompile, no chip replacement.
>
> 适用：使用 XMC（武汉新芯）XM25QH128C flash（Manufacturer ID `0x20` / Device ID `0x7018`）的 ESP32-S3 板卡，症状为刷官方固件后无限重启。本例实测于 YD-ESP32-S3（N16R8）。

---

## TL;DR

**English**: Certain ESP32-S3 boards ship with XMC XM25QH128C SPI flash. Espressif's IDF bootloader keeps an XMC ID whitelist (`mfid 0x40/0x41/0x50`) that doesn't include this chip's `mfid 0x70`, so `bootloader_flash_xmc_startup()` always returns `ESP_FAIL` → infinite reboot (~23 reboots/sec). Flash erase/write/verify all work fine — the chip is electrically OK. Instead of recompiling IDF, we patched the official MicroPython v1.29.0 firmware binary directly: locate the function, overwrite 4 bytes with `movi.n a2,0; retw.n` (return ESP_OK immediately), recompute the image checksum & SHA256, flash at 0x0. Board fully revived. See [esp-idf issue #9952](https://github.com/espressif/esp-idf/issues/9952) for the same case.

**中文**：这批板子的 XMC flash 不在 IDF bootloader 的兼容白名单里，每次上电都被判定"启动失败"而无限重启。不重编译、不换硬件，直接在官方固件二进制里把那 4 个字节改掉（函数进门就返回成功），重算校验后烧回——板子复活。

---

## 1. 症状

刷入 MicroPython 官方固件（`ESP32_GENERIC_S3-SPIRAM_OCT-v1.29.0.bin`，烧到 `0x0`）后，串口每秒输出约 23 次同样的日志：

```
rst:0x3 (RTC_SW_SYS_RST),boot:0x9 (SPI_FAST_FLASH_BOOT)
saved pc:0x403ccf09
mode:DIO, clock div:2
E (32) bootloader_flash: XMC flash startup fail
E (32) boot.esp32s3: failed when running XMC startup flow, reboot!
```

最反常的地方：**擦除、写入、MD5 校验全部成功**——`esptool` 每一步都返回 `Hash of data verified`。芯片能读能写，就是起不来。

## 2. 板卡信息

| 项目 | 值 |
| --- | --- |
| 开发板 | YD-ESP32-S3 COREBOARD V1.4（某宝第三方） |
| 模组 | ESP32-S3-WROOM-1 **N16R8**（16MB flash + 8MB Octal PSRAM） |
| flash 芯片 | **XMC XM25QH128C**，Manufacturer `0x20`，Device `0x7018` |
| 芯片版本 | ESP32-S3 rev v0.2（ROM `esp32s3-20210327`，早期批次） |
| 串口 | CH343P，115200 |
| 出厂状态 | 出厂固件可正常运行（出厂批次实测为 GigaDevice `0xc8` flash，厂家测试日志可证） |

## 3. 排查过程（全部失败，但排除了所有"设置问题"）

| 实验 | 结果 |
| --- | --- |
| 换固件：v1.19.1 / v1.29.0 标准版 / v1.29.0 SPIRAM_OCT | 同一个错 |
| 烧回厂家定制固件 `YD-ESP32-S3-N16R8-MPY-V1.1.bin`（在下载工具 `dl_temp` 里找到本体） | 同一个错 → **不是固件版本问题** |
| SPI 模式：DIO / DOUT / QIO；频率：80 / 40 / 20MHz | DIO/DOUT 同错；QIO 直接 ROM 读失败 |
| 先 ERASE 整片再烧 / 显式指定 16MB | 同一个错 |
| 厂家自带 Arduino 四件套（bootloader 无 XMC 检查代码） | 无报错但进 entry 后静默复位（XMC 支持被编译关闭 → 芯片被禁用） |
| 只烧 app 跳过 bootloader，让 ROM 直接加载 | ROM 加载全部 6 段无 XMC 报错，app 自己初始化 flash 时依然卡死 |

两个关键副产物：

1. **这颗 flash 只认 DIO**：QIO 模式连 ROM 都读不动（QE 位写不进），据此确认 DIO 40MHz 是它唯一能跑的配置。
2. **esptool 连接时也报同款警告**：`XMC flash chip boot-up failure detected! ... Could not confirm XMC flash chip boot-up fix` —— esptool 和 IDF 用的是同一套判断逻辑，可以直接看 esptool 源码定位根因。

## 4. 根因（源码级）

esptool `cmds.py` 中 `is_xmc_chip_strict()`（IDF bootloader 同款逻辑）：

```c
if (mfid == 0x40) { ... }        // XM25QHxxA
else if (mfid == 0x41) { ... }
else if (mfid == 0x50) { ... }
// 没有 0x70 ！
```

这颗 XM25QH128C 的 `device_id = 0x7018` → `mfid = 0x70`、`cpid = 0x18`，**不在白名单** → `bootloader_flash_xmc_startup()` 永远返回 `ESP_FAIL` → reboot。

乐鑫官方 issue [#9952](https://github.com/espressif/esp-idf/issues/9952)（IDFGH-8494）有一模一样的案例（Manufacturer `0x20` / Device `0x7018` / 16MB），社区给出的修法就是补一个分支：

```c
else if (mfid == 0x70) {
    if (cpid >= 0x17 && cpid <= 0x20) matched = true;
}
```

为什么 Kconfig 开关也救不了：

| `CONFIG_BOOTLOADER_FLASH_XMC_SUPPORT` | 结局 |
| --- | --- |
| 开启 | 执行 XMC 启动流程 → 白名单不含 0x70 → ESP_FAIL → 无限重启 |
| 关闭 | 文档原话："禁用后 XMC 芯片将被禁止使用" → 起不来 |

两头堵死。正常解法是用打过补丁的 IDF 重新编译固件，但第三方卖家提供不了，自装工具链又要下几 GB——于是直接改机器码。

## 5. 补丁原理

### 5.1 定位函数（不用工具链，capstone 即可）

```
报错字符串 "XMC flash startup fail"
  → 在 DROM 段 0x3fce353b
  → 在代码段字面量池里找到指向它的 4 字节指针（0x403cb72c）
  → 用 capstone（pip install --pre -U capstone，6.x 支持 Xtensa）反汇编
  → 找到引用该字面量的 l32r 指令（0x403cbf16）
  → 回溯 entry 指令 → 函数入口 0x403cbe98
```

函数原貌（节选）：

```
0x403cbe98  entry a1, 0x20        ; 函数入口
0x403cbe9b  l32r  a7, ...         ; 读 flash 芯片 ID ← 补丁写在这
0x403cbea4  call8 is_xmc_chip     ; 白名单判断
0x403cbea7  beqz  a10, +7         ; 不是 XMC → 返回 ESP_OK（返回值 0）
0x403cbeaa  movi.n a2, 0          ; ← "返回 0" 的现成字节：0c 02
0x403cbeac  retw.n                ; ← 现成字节：1d f0
0x403cbeb9  bnei  a10, 0x20       ; 检查厂商 ID
   ...（一串 XMC 修复命令 0xb9/0x79/0xff...）
   movi.n a2, -1                  ; 失败 → ESP_FAIL → reboot
```

### 5.2 打补丁

把 `0x403cbe9b`（entry 之后）的 4 个字节替换为 **`0c 02 1d f0`**（= `movi.n a2,0; retw.n`）——函数进门就返回 ESP_OK，整段 XMC 检查跳过。

字节序列直接抄自函数内部已有的指令（L86 `movi.n a2,0` + L87 `retw.n` 的编码），**指令编码零风险**，等效于官方 issue #9952 的白名单补丁。

### 5.3 重算镜像校验（改完不重算必变砖）

ESP32 镜像格式（先在原文件上验证公式，对不上立即中止）：

- **checksum** = `0xEF ⊕ 所有段数据`（逐字节 XOR），存放在段结束后、按"校验字节之后 16 字节对齐"填充的位置（本例段结束 `0x4afc` → 校验字节在 `0x4aff`）
- **SHA256 digest** = 对 `[0, 校验字节+1)` 计算，存于其后 32 字节
- 段表从文件偏移 24 开始（S3 有 16 字节扩展头），bootloader 共 3 段

## 6. 使用方法

```bash
pip install esptool "capstone>=6.0.0" --pre --upgrade

# 1. 打补丁（脚本内置原字节断言，固件不对会自动中止）
python make_patch.py                       # 默认处理 ESP32_GENERIC_S3-SPIRAM_OCT-v1.29.0.bin
python make_patch.py --fw 你的固件.bin     # 指定其他固件（函数地址会变，见 §8）

# 2. 烧录（只烧 0x0 一个文件）
python -m esptool --port COM10 --chip esp32s3 -b 460800 erase_flash
python -m esptool --port COM10 --chip esp32s3 -b 460800 write_flash -z 0x0 ESP32_S3_XMC_bypass-v1.29.0.bin

# 3. 验证
python -m esptool --port COM10 --chip esp32s3 flash_id   # 确认 Manufacturer 0x20 / Device 0x7018
# 打开串口 115200，看到 "MicroPython v1.29.0 ... >>>" 即成功
```

本仓库附带的 `ESP32_S3_XMC_bypass-v1.29.0.bin` 是已打补丁、可直接烧的成品（N16R8/SPIRAM_OCT 变体）。

## 7. 验证结果

| 测试 | 结果 |
| --- | --- |
| XMC 报错 / 重启循环 | 0 次（之前每秒 23 次） |
| REPL（UART0） | `>>>` 正常交互 |
| flash / PSRAM | 16MB + 八线 8MB（空闲 8.3MB） |
| 板载 WS2812（GPIO48） | 可点亮 |
| 文件系统 + `main.py` 自启 | 正常 |

## 8. 已知限制与提醒

- **QIO 不可用**：这颗 flash 的 QE 位写不进，只能 DIO 40MHz 运行（速度略降，功能无碍）。
- **别刷官方未打补丁的固件**：刷了就回到重启循环。恢复办法：重烧本仓库的 bin，或用 `make_patch.py` 重新打补丁。
- **换固件版本要重新定位**：函数地址随版本变化。定位流程见 §5.1，脚本里的 `assert old == ...` 原字节校验就是为了防止你在错误地址上硬写。
- **改二进制有风险**：本方法仅在 v1.29.0 SPIRAM_OCT 上验证过。改错一个字节可能变砖，自己动手前先确认你的 flash ID 也是 `0x20/0x7018`（`esptool flash_id` 一看便知）。
- 谨慎怀疑卖家也修不了：他们没有乐鑫源码级固件，出路要么换 GigaDevice/Winbond 批次的板，要么就是本文这条路。

## 9. 你的板子也这样？先做三件事

1. `python -m esptool --port COMx --chip esp32s3 flash_id` —— 看 Manufacturer 是不是 `0x20`、Device 是不是 `0x7018`；
2. 串口 115200 监听上电日志 —— 是否反复打印 `XMC flash startup fail`；
3. 两条都命中 → 按本文流程走；不命中 → 你的是别的问题（别乱套用补丁）。

## 10. 参考

- [espressif/esp-idf#9952](https://github.com/espressif/esp-idf/issues/9952)（IDFGH-8494，同型号同报错，含官方白名单补丁）
- [esp-adf#901](https://github.com/espressif/esp-adf/issues/901)（AUD-4144，同系列）
- esptool 源码：`loader.py:576 checksum()`、`bin_image.py read_checksum/append_checksum`、`cmds.py is_xmc_chip_strict()`
- [MicroPython 官方固件](https://micropython.org/download/ESP32_GENERIC_S3_SPIRAM_OCT/)（MIT License，重新分发补丁版固件合法）
- YD-ESP32-S3 原理图 V1.4（随板资料包）

---

*固件本身版权归 MicroPython Software Foundation（MIT）。补丁脚本与文档可自由使用，风险自负。*
