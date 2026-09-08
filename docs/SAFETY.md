# SAFETY — 必读

刷机顺序永远是：**确认机型 → 备份 → 干跑检查 → 二次确认 → 刷入 → 校验**。

## 1. 边界保护规则

- 仅 9008 放行：`9008/900E/901D/901F/...`。fastboot/adb 下所有写按钮锁定。
- Loader 校验：存在 + `.elf/.mbn/.bin` + 非空。Sahara 握手失败立即停。
- 黑名单分区默认拒绝单写：`modemst1/modemst2/fsg/fsc/persist/devinfo/limits/sns/sid_*/storsec/secdata/keymaster*/cmnlib*/gpt`。
- `rawprogram/patch` 干跑：`qfil` 前检查文件存在，缺失即停。
- 二次确认：整包、单写、备份全盘都要 `Yes`，默认 `No`。

## 2. 备份要求

- 整包前必须：`printgpt` → `gpt <backup>` → `rl <backup> --skip=userdata,metadata`。
- 单写前必须：`r <part> <backup>/<part>.img`。
- 产物：`~/.flash-device/backups/<dev>_<ts>/{*.bin,*.img,rawprogram*.xml,MANIFEST.json,sha256sums.txt}`。
- 恢复：用备份目录的 `gpt` + 单分区 `w` 写回，或整目录 `wl`（需核对 lun）。

## 3. 进度解读

- 总进度条 = 文件序号 + 文件内百分比综合；当前文件条 = EDL `Progress:` 行。
- `op` 列显示 `Write/Read/Erase/qfil-write`，文件名来自 `[qfil] programming X`。
- 卡住超过 5 分钟无 `%` 更新：不要拔线，先看日志最后 20 行，多数是 loader 不匹配。

## 4. 禁忌

- 机型/loader 不确定不刷；电量低不刷；Hub 转接不刷；固件目录混机型不刷。
- 密码、token、私有固件永不进仓，不贴日志中的序列号到公开 issue。
