# XiaoAI Plus（豆包改造版）

小爱音箱 Pro 的端侧程序，基于 [kslr/xiaoai-plus](https://github.com/kslr/xiaoai-plus) 修改，接入火山引擎豆包实时语音（O2 / Seeduplex），并针对中文对话做了配置化改造。

## 作者与来源

- **上游项目**：[kslr/xiaoai-plus](https://github.com/kslr/xiaoai-plus)，作者 **kslr**，MIT License，`LICENSE` 原样保留（Copyright (c) 2026 kslr）。
- **本目录的豆包改造**：`xiaoai-doubao` 项目，包含 O2 Seeduplex 接入、API Key 鉴权、`playmate` 提示词开关、中文 `system_role` / `speaking_style` 支持、记忆后台对接等。
- **AEC 参考**：[OwnDing/open-xiaoai](https://github.com/OwnDing/open-xiaoai)（MIT），`src/audio/subband_aec.*` 移植自其小智示例；同时参考了 [idootop/open-xiaoai](https://github.com/idootop/open-xiaoai)（MIT）。
- **端侧推理**：[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）与 [ONNX Runtime](https://github.com/microsoft/onnxruntime)（MIT）。
- 其他第三方组件与许可详见同目录的 `THIRD_PARTY.md`。

## 目录

```
src/                 C++ 源码（realtime / audio / wakeup / memory / config）
tests/               离线测试与主机侧测试
toolchain/           Docker 交叉编译工具链
assets/              唤醒词等小文件（不含模型权重）
keywords/            唤醒词/退出词列表
config.ini.example   配置示例
boot.sh              音箱上的启动脚本
```

KWS 模型权重、`tokens.txt`、`en.phone` 不随源码分发，需要从 sherpa-onnx 官方模型归档自行下载。

## 配置

```ini
[realtime]
protocol = o2
api_key = your_api_key
model = 1.2.6.1
voice = zh_female_vv_uranus_bigtts
bot_name = 豆包

# off：使用下面的 system_role / speaking_style，适合中文
# on ：保留原有的儿童英语玩伴提示词
playmate = off

system_role = 你是一个中文语音助手，请始终用简洁自然的中文回答。
speaking_style = 语气自然、友好、简洁，每次不超过两句话。
```

`playmate = off` 时不会注入内置的英文儿童提示词，回答语言由 `system_role` / `speaking_style` 决定。

## 构建

需要 Docker、`arm-linux-gnueabihf` 交叉工具链和 **gold 链接器**（`binutils` 自带）。

```bash
make -C toolchain build
./toolchain/build-sherpa-static.sh
make -C toolchain compile
```

本目录的 `armv7-linux-gnueabihf.cmake` 已启用 `-Wl,--gc-sections` 和 `-fuse-ld=gold`：静态 glibc 的 `memcpy` IFUNC 在大的 Thumb-2 程序里用 bfd 链接会在设备上触发 `Illegal instruction`，gold 可以规避。

如果使用的是 sherpa 构建时下载的预编译 onnxruntime，并遇到符号名被截断导致的链接错误，需要用同一套交叉工具链从源码重建 onnxruntime，替换 `assets/sherpa_onnx_static/lib/libonnxruntime.a` 后再链接。

## License

本目录代码遵循根目录 `LICENSE`（MIT，上游版权归 kslr）。第三方组件继续遵循各自许可证；KWS 模型权重有独立许可，使用前请自行确认。

